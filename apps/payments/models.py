# import uuid
# from django.db import models


# class DepositSession(models.Model):
#     class Method(models.TextChoices):
#         PAYSTACK = 'paystack', 'Paystack'
#         FLUTTERWAVE = 'flutterwave', 'Flutterwave'
#         BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
#         USDT = 'usdt', 'USDT'

#     class Status(models.TextChoices):
#         PENDING = 'pending', 'Pending'
#         COMPLETED = 'completed', 'Completed'
#         FAILED = 'failed', 'Failed'
#         EXPIRED = 'expired', 'Expired'

#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     user = models.ForeignKey(
#         'users.User', on_delete=models.CASCADE, related_name='deposit_sessions'
#     )
#     amount = models.DecimalField(max_digits=15, decimal_places=2)
#     method = models.CharField(max_length=20, choices=Method.choices)

#     # Provider's transaction reference (used for idempotency on webhooks)
#     provider_reference = models.CharField(
#         max_length=200, unique=True, db_index=True
#     )
#     payment_url = models.TextField(blank=True)

#     status = models.CharField(
#         max_length=20, choices=Status.choices, default=Status.PENDING
#     )
#     webhook_received_at = models.DateTimeField(null=True, blank=True)
#     completed_at = models.DateTimeField(null=True, blank=True)
#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         db_table = 'deposit_sessions'
#         ordering = ['-created_at']

#     def __str__(self):
#         return f'Deposit({self.user}, ₦{self.amount}, {self.status})'
"""
Payments module models.

The Deposit table is polymorphic across all three providers — Paystack,
Monnify, NOWPayments — with a `provider` field discriminating, and a
`provider_data` JSONField holding raw provider-specific payload for audit.

VirtualAccount is Monnify-specific: each user gets a dedicated bank account
number that automatically routes inbound transfers to their wallet.
"""
import uuid
from decimal import Decimal

from django.db import models


class Deposit(models.Model):
    """
    A user-initiated deposit.

    Lifecycle:
        PENDING  → user has initiated, waiting for payment
        COMPLETED → webhook received, wallet credited
        FAILED    → provider reported failure
        EXPIRED   → user never paid, deposit window closed

    Idempotency anchor: `internal_reference` (we generate) is unique. The
    `provider_reference` may also be set (provider returns it) and is also
    indexed for webhook lookups.
    """

    class Provider(models.TextChoices):
        PAYSTACK = 'paystack', 'Paystack'
        MONNIFY = 'monnify', 'Monnify'
        NOWPAYMENTS = 'nowpayments', 'NOWPayments'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        EXPIRED = 'expired', 'Expired'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User', on_delete=models.CASCADE, related_name='deposits',
    )

    # Amount in NGN that will be credited to the user's wallet on success.
    amount = models.DecimalField(max_digits=20, decimal_places=2)

    provider = models.CharField(max_length=20, choices=Provider.choices)

    # Reference WE generate. Always unique. Used as wallet idempotency key.
    internal_reference = models.CharField(
        max_length=200, unique=True, db_index=True,
    )
    # Reference the PROVIDER returns (Paystack reference, Monnify
    # transactionReference, NOWPayments payment_id). May arrive after
    # initiation; populated when provider confirms.
    provider_reference = models.CharField(
        max_length=200, blank=True, default='', db_index=True,
    )

    # Where the user goes to pay (Paystack), or info to transfer to
    # (Monnify account number / NOWPayments crypto address).
    payment_url = models.TextField(blank=True, default='')
    payment_address = models.TextField(blank=True, default='')

    # ── Crypto-specific fields (null for fiat) ─────────────────────────
    # The amount in the original currency (e.g. 100 USDT).
    original_amount = models.DecimalField(
        max_digits=30, decimal_places=8, null=True, blank=True,
    )
    original_currency = models.CharField(
        max_length=10, blank=True, default='',
    )
    # NGN per unit of original_currency, locked at initiation time.
    conversion_rate = models.DecimalField(
        max_digits=20, decimal_places=8, null=True, blank=True,
    )

    # ── Audit & traceability ───────────────────────────────────────────
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    # Raw webhook payload for compliance and debugging. Provider-specific.
    provider_data = models.JSONField(default=dict, blank=True)
    # Bidirectional link to the wallet ledger entry (set on COMPLETED).
    wallet_transaction = models.OneToOneField(
        'wallet.Transaction',
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='deposit',
    )

    webhook_received_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'deposits'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['provider', 'status']),
            models.Index(fields=['user', 'created_at']),
        ]

    def __str__(self):
        return (
            f'Deposit({self.user}, ₦{self.amount}, {self.provider}, '
            f'{self.status})'
        )


class VirtualAccount(models.Model):
    """
    A Monnify-issued dedicated bank account assigned to a single user.

    When the user transfers money into this account from any Nigerian bank,
    Monnify fires a webhook and we credit their wallet. Account is permanent
    once created.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        'users.User',
        on_delete=models.CASCADE,
        related_name='virtual_account',
    )

    # Monnify's internal reference for the reserved account.
    monnify_account_reference = models.CharField(
        max_length=200, unique=True, db_index=True,
    )

    # The actual bank details we display to the user.
    account_number = models.CharField(max_length=20)
    account_name = models.CharField(max_length=200)
    bank_name = models.CharField(max_length=100)
    bank_code = models.CharField(max_length=10, blank=True, default='')

    # Raw payload from Monnify for audit.
    provider_data = models.JSONField(default=dict, blank=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'virtual_accounts'

    def __str__(self):
        return (
            f'VA({self.user}: {self.account_number} {self.bank_name})'
        )