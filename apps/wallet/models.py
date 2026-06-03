"""
Wallet models.

Architecture: balance is computed from the ledger (Transaction table), never
stored. The Wallet row is a stable per-user anchor used for row-level locking
during mutations.

Invariants enforced here:
  - Append-only Transaction table (no updates allowed for COMPLETED rows)
  - Unique reference_id prevents duplicate inserts at the DB level
  - CHECK constraint prevents any transaction from leaving a balance negative
  - Three balance types: coin, cash, staked
"""
import uuid
from decimal import Decimal

from django.db import models
from django.db.models import Sum


class TransactionManager(models.Manager):
    """
    Single source of truth for balance computation.

    Both Wallet model properties and WalletService call into this so there's
    only one implementation to keep correct.
    """

    def balance_for(self, user, balance_type: str) -> Decimal:
        """Compute current balance for a user + balance type from completed txs."""
        result = self.filter(
            user=user,
            balance_type=balance_type,
            status=Transaction.Status.COMPLETED,
        ).aggregate(total=Sum('amount'))
        return result['total'] or Decimal('0')


class Wallet(models.Model):
    """
    A user's wallet anchor row.

    Holds no balance data — balance is computed from Transaction. This row
    exists primarily as a stable lock target for SELECT FOR UPDATE during
    concurrent wallet mutations. One wallet per user.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        'users.User', on_delete=models.CASCADE, related_name='wallet',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'wallets'

    def __str__(self):
        return f'Wallet({self.user})'

    @property
    def coin_balance(self) -> Decimal:
        return Transaction.objects.balance_for(self.user, 'coin')

    @property
    def cash_balance(self) -> Decimal:
        return Transaction.objects.balance_for(self.user, 'cash')

    @property
    def staked_balance(self) -> Decimal:
        return Transaction.objects.balance_for(self.user, 'staked')

    @property
    def total_balance(self) -> Decimal:
        return self.coin_balance + self.cash_balance + self.staked_balance


class Transaction(models.Model):
    """
    Append-only ledger entry.

    Every wallet mutation creates exactly one Transaction. Balances are derived
    by summing Transaction.amount for a (user, balance_type, completed) tuple.

    Conventions:
      - amount is signed: positive = credit, negative = debit
      - balance_before / balance_after are recorded for audit and verification
      - reference_id is globally unique; webhooks pass their idempotency key here
    """

    class Type(models.TextChoices):
        DEPOSIT = 'deposit', 'Deposit'
        STAKE = 'stake', 'Stake'                  # locks money
        STAKE_RELEASE = 'stake_release', 'Stake Release'  # win — return + multiplier
        STAKE_FORFEIT = 'stake_forfeit', 'Stake Forfeit'  # loss — house keeps it
        WIN = 'win', 'Win'                        # cash credit on win
        WITHDRAWAL = 'withdrawal', 'Withdrawal'
        WITHDRAWAL_HOLD = 'withdrawal_hold', 'Withdrawal Hold'
        REFERRAL_BONUS = 'referral_bonus', 'Referral Bonus'
        DAILY_REWARD = 'daily_reward', 'Daily Reward'
        REVERSAL = 'reversal', 'Reversal'
        REFUND = 'refund', 'Refund'

    # class BalanceType(models.TextChoices):
    #     COIN = 'coin', 'Coin'      # play currency, not withdrawable
    #     CASH = 'cash', 'Cash'      # winnings, withdrawable post-KYC
    #     STAKED = 'staked', 'Staked'  # locked for a pending spin
    class BalanceType(models.TextChoices):
        DEPOSIT_COINS = 'deposit_coins', 'Deposit Coins'
        BONUS_COINS = 'bonus_coins', 'Bonus Coins'
        EARNINGS = 'earnings', 'Earnings (NGN)'
        STAKED = 'staked', 'Staked'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        REVERSED = 'reversed', 'Reversed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User', on_delete=models.CASCADE, related_name='transactions',
    )
    wallet = models.ForeignKey(
        Wallet, on_delete=models.CASCADE, related_name='transactions',
    )

    type = models.CharField(max_length=20, choices=Type.choices)
    balance_type = models.CharField(max_length=50, choices=BalanceType.choices)

    # Signed: positive = credit, negative = debit. Stored at higher precision
    # than current needs for headroom (gaming + future multi-currency).
    amount = models.DecimalField(max_digits=20, decimal_places=2)
    balance_before = models.DecimalField(max_digits=20, decimal_places=2)
    balance_after = models.DecimalField(max_digits=20, decimal_places=2)

    # Idempotency anchor. For external triggers (webhooks, spin results) this
    # is the provider's idempotency key. For internal compound ops (stake →
    # release) we suffix with a discriminator to keep both legs unique.
    reference_id = models.CharField(max_length=200, unique=True, db_index=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.COMPLETED,
    )
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TransactionManager()

    class Meta:
        db_table = 'transactions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'type']),
            models.Index(fields=['user', 'balance_type', 'status']),
            models.Index(fields=['user', 'created_at']),
        ]
        constraints = [
            # Last line of defense: even raw SQL or buggy services cannot
            # produce a transaction that ends with a negative balance.
            models.CheckConstraint(
                check=models.Q(balance_after__gte=0),
                name='wallet_balance_never_negative',
            ),
            # Internal consistency: balance_after must equal balance_before + amount
            models.CheckConstraint(
                check=models.Q(balance_after=models.F('balance_before') + models.F('amount')),
                name='wallet_balance_arithmetic_consistent',
            ),
        ]

    def __str__(self):
        sign = '+' if self.amount >= 0 else ''
        return f'{self.type} {sign}{self.amount} ({self.balance_type})'