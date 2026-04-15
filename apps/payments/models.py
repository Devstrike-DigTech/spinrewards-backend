import uuid
from django.db import models


class DepositSession(models.Model):
    class Method(models.TextChoices):
        PAYSTACK = 'paystack', 'Paystack'
        FLUTTERWAVE = 'flutterwave', 'Flutterwave'
        BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
        USDT = 'usdt', 'USDT'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        EXPIRED = 'expired', 'Expired'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User', on_delete=models.CASCADE, related_name='deposit_sessions'
    )
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices)

    # Provider's transaction reference (used for idempotency on webhooks)
    provider_reference = models.CharField(
        max_length=200, unique=True, db_index=True
    )
    payment_url = models.TextField(blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    webhook_received_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'deposit_sessions'
        ordering = ['-created_at']

    def __str__(self):
        return f'Deposit({self.user}, ₦{self.amount}, {self.status})'
