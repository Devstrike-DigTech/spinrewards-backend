# import uuid
# from django.db import models


# class WithdrawalRequest(models.Model):
#     class Status(models.TextChoices):
#         PENDING = 'pending', 'Pending'
#         PROCESSING = 'processing', 'Processing'
#         COMPLETED = 'completed', 'Completed'
#         FAILED = 'failed', 'Failed'
#         REVERSED = 'reversed', 'Reversed'

#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     user = models.ForeignKey(
#         'users.User', on_delete=models.CASCADE, related_name='withdrawal_requests'
#     )
#     amount = models.DecimalField(max_digits=15, decimal_places=2)
#     bank_account = models.CharField(max_length=20)
#     bank_code = models.CharField(max_length=10)
#     bank_name = models.CharField(max_length=100, blank=True)
#     account_name = models.CharField(max_length=200, blank=True)

#     reference = models.CharField(max_length=100, unique=True, db_index=True)
#     provider_reference = models.CharField(max_length=200, blank=True)

#     status = models.CharField(
#         max_length=20, choices=Status.choices, default=Status.PENDING
#     )
#     failure_reason = models.TextField(blank=True)

#     transaction = models.OneToOneField(
#         'wallet.Transaction', on_delete=models.SET_NULL,
#         null=True, blank=True, related_name='withdrawal',
#     )

#     created_at = models.DateTimeField(auto_now_add=True)
#     processed_at = models.DateTimeField(null=True, blank=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         db_table = 'withdrawal_requests'
#         ordering = ['-created_at']

#     def __str__(self):
#         return f'Withdrawal({self.user}, ₦{self.amount}, {self.status})'
"""
Withdrawal models.

Lifecycle states:
    pending_review   Awaiting admin approval (manual review path OR amount ≥ threshold)
    pending          Approved (or auto-approved). Cash debited. Ready for processing.
    processing       Payout dispatched to Paystack. Waiting for confirmation.
    completed        Paystack confirmed delivery to user's bank.
    failed           Paystack rejected the transfer.
    rejected         Admin manually rejected. Cash returned.
    cancelled        User cancelled before processing.

Funds movement:
    Request:           cash_balance → debited (held in withdrawal record)
    Completion:        gone (paid out)
    Failure/rejection: refunded back to cash_balance
"""
import uuid
from decimal import Decimal

from django.db import models
from django.db.models import Q


class Withdrawal(models.Model):

    class Status(models.TextChoices):
        PENDING_REVIEW = 'pending_review', 'Pending Review (Admin)'
        PENDING = 'pending', 'Pending Processing'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        REJECTED = 'rejected', 'Rejected'
        CANCELLED = 'cancelled', 'Cancelled'

    TERMINAL_STATES = ('completed', 'failed', 'rejected', 'cancelled')
    ACTIVE_STATES = ('pending_review', 'pending', 'processing')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User', on_delete=models.PROTECT, related_name='withdrawals',
    )
    bank_account = models.ForeignKey(
        'kyc.BankAccount', on_delete=models.PROTECT,
        related_name='withdrawals',
    )

    amount = models.DecimalField(max_digits=20, decimal_places=2)
    fee = models.DecimalField(
        max_digits=20, decimal_places=2, default=Decimal('0.00'),
    )
    net_amount = models.DecimalField(max_digits=20, decimal_places=2)

    status = models.CharField(
        max_length=20, choices=Status.choices,
        default=Status.PENDING,
    )

    # Provider integration
    provider = models.CharField(max_length=20, default='paystack')
    provider_recipient_id = models.CharField(max_length=100, blank=True)
    provider_transfer_id = models.CharField(max_length=100, blank=True)
    provider_response = models.JSONField(default=dict, blank=True)

    # Reference & idempotency
    reference = models.CharField(max_length=200, unique=True, db_index=True)

    # Wallet linkage
    debit_transaction = models.ForeignKey(
        'wallet.Transaction', on_delete=models.PROTECT,
        related_name='withdrawal_debits', null=True, blank=True,
    )
    refund_transaction = models.ForeignKey(
        'wallet.Transaction', on_delete=models.PROTECT,
        related_name='withdrawal_refunds', null=True, blank=True,
    )

    # Approval audit
    requires_review = models.BooleanField(default=False)
    forced_manual_review = models.BooleanField(
        default=False,
        help_text='True if user submitted via manual-review endpoint.',
    )
    reviewed_by = models.ForeignKey(
        'users.User', on_delete=models.SET_NULL,
        related_name='reviewed_withdrawals', null=True, blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.CharField(max_length=500, blank=True)

    # Failure audit
    failure_reason = models.CharField(max_length=500, blank=True)

    # Timestamps
    requested_at = models.DateTimeField(auto_now_add=True)
    processing_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'withdrawals'
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['user', '-requested_at']),
            models.Index(fields=['status']),
            models.Index(fields=['provider_transfer_id']),
        ]
        constraints = [
            models.CheckConstraint(
                check=Q(amount__gt=0),
                name='withdrawal_amount_positive',
            ),
            models.CheckConstraint(
                check=Q(net_amount__gte=0),
                name='withdrawal_net_amount_non_negative',
            ),
        ]

    def __str__(self):
        return f'Withdrawal({self.user.telegram_id}, ₦{self.amount}, {self.status})'

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATES