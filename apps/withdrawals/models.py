
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

    class Rail(models.TextChoices):
        BANK = 'bank', 'Bank Transfer (NGN)'
        CRYPTO = 'crypto', 'Crypto (USDT TRC-20)'
 
    class Currency(models.TextChoices):
        NGN = 'NGN', 'Nigerian Naira'
        USDT = 'USDT', 'Tether USD'
 
    class Network(models.TextChoices):
        TRC20 = 'TRC20', 'TRON USDT'
        # ERC20 = 'ERC20', 'Ethereum USDT'   # future
        # BEP20 = 'BEP20', 'BNB Chain USDT'  # future

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
        'kyc.BankAccount',
        on_delete=models.PROTECT,
        related_name='withdrawals',
        null=True,               
        blank=True,               
        help_text='Required for bank rail; null for crypto rail.',
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
    rail = models.CharField(
        max_length=10,
        choices=Rail.choices,
        default=Rail.BANK,
        db_index=True,
        help_text='Bank transfer or crypto on-chain.',
    )
 
    currency = models.CharField(
        max_length=8,
        choices=Currency.choices,
        default=Currency.NGN,
        db_index=True,
        help_text='Withdrawal currency (NGN for bank, USDT for crypto).',
    )
 
    # ─── v3: Crypto-only fields (empty for bank withdrawals) ─────────
    wallet_address = models.CharField(
        max_length=120,
        blank=True,
        default='',
        help_text='User crypto wallet (e.g. TRC-20 USDT). Empty for bank.',
    )
 
    network = models.CharField(
        max_length=20,
        choices=Network.choices,
        blank=True,
        default='',
        help_text='Crypto network. Empty for bank.',
    )
 
    tx_hash = models.CharField(
        max_length=120,
        blank=True,
        default='',
        db_index=True,
        help_text='On-chain transaction hash after payout (crypto only).',
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
                check=(
                    Q(rail='bank', bank_account__isnull=False)
                    | Q(rail='crypto', wallet_address__gt='')
                ),
                name='withdrawal_rail_fields_present',
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
    

class CryptoWallet(models.Model):
    """
    User-saved crypto wallet address for re-use across withdrawals.
 
    Currently TRC-20 only. Add more networks as enums grow.
    Multiple addresses per user; one can be is_default.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='crypto_wallets',
    )
 
    network = models.CharField(
        max_length=20,
        choices=Withdrawal.Network.choices,
        default=Withdrawal.Network.TRC20,
        db_index=True,
    )
 
    address = models.CharField(
        max_length=120,
        help_text='Crypto wallet address. TRC-20 addresses are 34 chars starting with T.',
    )
 
    label = models.CharField(
        max_length=50,
        blank=True,
        default='',
        help_text="Optional user-supplied label (e.g. 'My Binance wallet').",
    )
 
    is_default = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Default wallet for this user — picked automatically if no address specified.',
    )
 
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text='Soft-delete flag. Inactive wallets are hidden from selection but kept for audit.',
    )
 
    verified_at = models.DateTimeField(
        null=True, blank=True,
        help_text=(
            'When this address was first verified. For now: set on save after '
            'format validation. Future: small test-transaction confirmation.'
        ),
    )
 
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
 
    class Meta:
        db_table = 'crypto_wallets'
        ordering = ['-is_default', '-created_at']
        indexes = [
            models.Index(fields=['user', 'is_active']),
            models.Index(fields=['user', 'is_default']),
        ]
        constraints = [
            # Same address can't be saved twice by the same user on the same network
            models.UniqueConstraint(
                fields=['user', 'network', 'address'],
                condition=Q(is_active=True),
                name='unique_active_crypto_wallet_per_user',
            ),
            # Only one default per user
            models.UniqueConstraint(
                fields=['user'],
                condition=Q(is_default=True, is_active=True),
                name='one_default_crypto_wallet_per_user',
            ),
        ]
 
    def __str__(self):
        suffix = f' ({self.label})' if self.label else ''
        return f'CryptoWallet({self.user.telegram_id}, {self.network}, ...{self.address[-6:]}{suffix})'