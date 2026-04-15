import uuid
from django.db import models


class RTPTier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50)
    stake_min = models.DecimalField(max_digits=15, decimal_places=2)
    stake_max = models.DecimalField(max_digits=15, decimal_places=2)
    # Informational only — not enforced by system
    rtp_target = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Informational RTP target. Not enforced.'
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        'users.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_tiers',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rtp_tiers'
        ordering = ['stake_min']

    def __str__(self):
        return f'{self.name} (₦{self.stake_min} – ₦{self.stake_max})'

    @property
    def computed_rtp(self):
        """Dynamically compute RTP from active outcomes: Σ(probability × multiplier)."""
        from decimal import Decimal
        outcomes = self.outcomes.filter(is_active=True)
        if not outcomes.exists():
            return Decimal('0')
        return sum(o.probability * o.multiplier / Decimal('100') for o in outcomes)


class RTPOutcome(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.ForeignKey(
        RTPTier, on_delete=models.CASCADE, related_name='outcomes'
    )
    label = models.CharField(max_length=50, help_text='e.g. Loss, Small Win, Jackpot')
    multiplier = models.DecimalField(
        max_digits=10, decimal_places=4,
        help_text='e.g. 0=loss, 0.5=half back, 2=2x, 20=jackpot'
    )
    probability = models.DecimalField(
        max_digits=7, decimal_places=4,
        help_text='Percentage probability, e.g. 40.0000 = 40%'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rtp_outcomes'
        ordering = ['multiplier']

    def __str__(self):
        return f'{self.label} ({self.multiplier}x @ {self.probability}%)'


class SpinResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User', on_delete=models.CASCADE, related_name='spin_results'
    )
    tier = models.ForeignKey(
        RTPTier, on_delete=models.SET_NULL, null=True, related_name='spin_results'
    )
    stake = models.DecimalField(max_digits=15, decimal_places=2)
    multiplier = models.DecimalField(max_digits=10, decimal_places=4)
    win_amount = models.DecimalField(max_digits=15, decimal_places=2)
    outcome_label = models.CharField(max_length=50)

    # Stored for audit/dispute purposes
    rng_value = models.DecimalField(max_digits=12, decimal_places=10)

    stake_transaction = models.OneToOneField(
        'wallet.Transaction', on_delete=models.CASCADE,
        related_name='spin_stake', null=True,
    )
    win_transaction = models.OneToOneField(
        'wallet.Transaction', on_delete=models.SET_NULL,
        related_name='spin_win', null=True, blank=True,
    )

    # Client-provided idempotency key
    idempotency_key = models.CharField(max_length=200, unique=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'spin_results'
        ordering = ['-created_at']

    def __str__(self):
        return f'Spin({self.user}, stake={self.stake}, win={self.win_amount})'

    @property
    def result(self):
        return 'win' if self.win_amount > 0 else 'loss'
