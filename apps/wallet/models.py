import uuid
from django.db import models
from django.db.models import Sum


class Wallet(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        'users.User', on_delete=models.CASCADE, related_name='wallet'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'wallets'

    def __str__(self):
        return f'Wallet({self.user})'

    def _compute_balance(self, balance_type: str):
        from decimal import Decimal
        result = self.transactions.filter(
            balance_type=balance_type,
            status=Transaction.Status.COMPLETED,
        ).aggregate(total=Sum('amount'))
        return result['total'] or Decimal('0')

    @property
    def coin_balance(self):
        return self._compute_balance('coin')

    @property
    def cash_balance(self):
        return self._compute_balance('cash')

    @property
    def total_balance(self):
        return self.coin_balance + self.cash_balance


class Transaction(models.Model):
    class Type(models.TextChoices):
        DEPOSIT = 'deposit', 'Deposit'
        STAKE = 'stake', 'Stake'
        WIN = 'win', 'Win'
        WITHDRAWAL = 'withdrawal', 'Withdrawal'
        REFERRAL_BONUS = 'referral_bonus', 'Referral Bonus'
        DAILY_REWARD = 'daily_reward', 'Daily Reward'
        REVERSAL = 'reversal', 'Reversal'

    class BalanceType(models.TextChoices):
        COIN = 'coin', 'Coin'
        CASH = 'cash', 'Cash'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        REVERSED = 'reversed', 'Reversed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User', on_delete=models.CASCADE, related_name='transactions'
    )
    wallet = models.ForeignKey(
        Wallet, on_delete=models.CASCADE, related_name='transactions'
    )
    type = models.CharField(max_length=20, choices=Type.choices)
    balance_type = models.CharField(max_length=10, choices=BalanceType.choices)

    # Positive = credit, Negative = debit
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    balance_before = models.DecimalField(max_digits=15, decimal_places=2)
    balance_after = models.DecimalField(max_digits=15, decimal_places=2)

    # Unique reference for idempotency
    reference_id = models.CharField(max_length=200, unique=True, db_index=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.COMPLETED
    )
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'transactions'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'type']),
            models.Index(fields=['user', 'balance_type']),
            models.Index(fields=['user', 'status']),
        ]

    def __str__(self):
        sign = '+' if self.amount >= 0 else ''
        return f'{self.type} {sign}{self.amount} ({self.balance_type})'
