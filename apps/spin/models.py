"""
Spin engine models.

Architecture:
    Wheel        — a configurable game (Standard, Power, Mega, Welcome, etc.)
    WheelSegment — a slice of a wheel (probability + multiplier)
    Spin         — a single execution of a wheel for a user

Wheel ranges follow inclusive-lower, exclusive-upper convention:
    Wheel A: [200, 500)   matches stakes 200 to 499
    Wheel B: [500, 1000)  matches stakes 500 to 999
    No overlap, no gaps. ₦500 lands on Wheel B.

Active wheels cannot have overlapping stake ranges. Enforced in clean().

Wheel configuration is DB-driven so the admin panel can adjust segments,
probabilities, and multipliers without redeploys. Probability is stored as
INTEGER WEIGHTS (not percentages) — this avoids floating-point edge cases
in selection and keeps math exact.

Provably fair is stubbed but not user-exposed in v1: the server seed is
generated, hashed, and stored. The reveal endpoint comes later.
"""
import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class Wheel(models.Model):
    """A configurable spin-the-wheel game."""

    class WheelType(models.TextChoices):
        STANDARD = 'standard', 'Standard'
        POWER = 'power', 'Power'
        MEGA = 'mega', 'Mega'
        WELCOME = 'welcome', 'Welcome'
        DAILY_CHALLENGE = 'daily_challenge', 'Daily Challenge'

    class CurrencyType(models.TextChoices):
        COIN = 'coin', 'Coin'   # play balance
        CASH = 'cash', 'Cash'   # withdrawable balance

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    wheel_type = models.CharField(max_length=20, choices=WheelType.choices)
    name = models.CharField(max_length=100)

    currency_type = models.CharField(
        max_length=10, choices=CurrencyType.choices,
        default=CurrencyType.COIN,
    )

    # Stake range. INCLUSIVE LOWER, EXCLUSIVE UPPER.
    #
    # A wheel matches stake `s` if:
    #     min_stake <= s < max_stake
    #
    # Examples:
    #   Wheel A: min=200, max=500   → matches 200, 250, 499  but NOT 500
    #   Wheel B: min=500, max=1000  → matches 500, 750, 999  but NOT 1000
    #
    # For Welcome wheel (free), both will be 0 (and is_welcome_only=True
    # bypasses range checks entirely).
    min_stake = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    max_stake = models.DecimalField(max_digits=20, decimal_places=2, default=0)

    is_welcome_only = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    rtp_target = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Informational target RTP percentage. Not enforced.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'wheels'
        ordering = ['min_stake']
        constraints = [
            # Stake ordering must be sane.
            models.CheckConstraint(
                check=Q(max_stake__gte=models.F('min_stake')),
                name='wheel_max_stake_gte_min',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.get_wheel_type_display()})'

    def clean(self):
        """
        Validation that runs from admin forms and explicit calls.

        Enforces:
          1. Welcome wheels have min_stake=0, max_stake=0
          2. Non-welcome active wheels have max_stake > min_stake
          3. No overlap with other active non-welcome wheels
        """
        super().clean()

        # Welcome wheel rules — bypass overlap checks
        if self.is_welcome_only:
            if self.min_stake != 0 or self.max_stake != 0:
                raise ValidationError({
                    'min_stake': 'Welcome wheels must have min_stake=0 and max_stake=0.',
                    'max_stake': 'Welcome wheels must have min_stake=0 and max_stake=0.',
                })
            return  # no further checks for welcome wheels

        # Non-welcome wheel must have a real range
        if self.max_stake <= self.min_stake:
            raise ValidationError({
                'max_stake': 'max_stake must be greater than min_stake.',
            })

        # Skip overlap check for inactive wheels
        if not self.is_active:
            return

        # Check overlap with OTHER active non-welcome wheels.
        # Two ranges [a, b) and [c, d) overlap iff a < d AND c < b.
        overlapping = Wheel.objects.filter(
            is_active=True,
            is_welcome_only=False,
            min_stake__lt=self.max_stake,    # other.min < self.max
            max_stake__gt=self.min_stake,    # other.max > self.min
        ).exclude(pk=self.pk)

        if overlapping.exists():
            other = overlapping.first()
            raise ValidationError({
                'min_stake': (
                    f'Stake range [{self.min_stake}, {self.max_stake}) '
                    f'overlaps with active wheel "{other.name}" '
                    f'[{other.min_stake}, {other.max_stake}). '
                    f'Active wheel ranges must not overlap.'
                ),
            })

    def save(self, *args, **kwargs):
        """Ensure validation runs even when save() is called directly."""
        self.full_clean()
        super().save(*args, **kwargs)

    def matches_stake(self, stake_amount: Decimal) -> bool:
        """Check if this wheel matches a stake using inclusive-lower, exclusive-upper."""
        if self.is_welcome_only:
            return stake_amount == 0
        return self.min_stake <= stake_amount < self.max_stake

    @property
    def computed_rtp(self) -> Decimal:
        """
        Actual RTP based on currently active segments:
            sum(weight × multiplier) / sum(weight) × 100

        Returns Decimal percentage. Equal to 100 means fair, <100 means
        house edge, >100 means players win on average.
        """
        segments = list(self.segments.filter(is_active=True))
        if not segments:
            return Decimal('0')
        total_weight = sum(s.probability_weight for s in segments)
        if total_weight == 0:
            return Decimal('0')
        weighted_payout = sum(
            Decimal(s.probability_weight) * s.multiplier for s in segments
        )
        return (weighted_payout / Decimal(total_weight) * Decimal('100')).quantize(
            Decimal('0.01')
        )


class WheelSegment(models.Model):
    """
    One slice of a wheel.

    Probability is stored as an INTEGER WEIGHT relative to other segments
    on the same wheel. Selection picks `secrets.randbelow(total_weight)`,
    no floating-point math.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wheel = models.ForeignKey(
        Wheel, on_delete=models.CASCADE, related_name='segments',
    )

    position = models.IntegerField()

    label = models.CharField(
        max_length=50,
        help_text='User-visible label, e.g. "Loss", "₦1000", "10x"',
    )

    multiplier = models.DecimalField(
        max_digits=10, decimal_places=4,
        help_text='0=loss, 1=push (stake back), 2+=win',
    )

    probability_weight = models.PositiveIntegerField(
        help_text='Integer weight relative to other segments on this wheel.',
    )

    color = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Hex color for client animation, e.g. "#C9961A"',
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'wheel_segments'
        ordering = ['wheel', 'position']
        constraints = [
            models.CheckConstraint(
                check=Q(probability_weight__gte=0),
                name='segment_weight_non_negative',
            ),
            models.CheckConstraint(
                check=Q(multiplier__gte=0),
                name='segment_multiplier_non_negative',
            ),
            models.UniqueConstraint(
                fields=['wheel', 'position'],
                name='unique_segment_position_per_wheel',
            ),
        ]

    def __str__(self):
        return f'{self.wheel.name} #{self.position}: {self.label} ({self.multiplier}×, w={self.probability_weight})'


class Spin(models.Model):
    """A single spin execution."""

    class Outcome(models.TextChoices):
        WIN = 'win', 'Win'
        LOSS = 'loss', 'Loss'
        PUSH = 'push', 'Push'
        PARTIAL_LOSS = 'partial_loss', 'Partial Loss'

    class SourceWallet(models.TextChoices):
        CRYPTO_COINS = 'crypto_coins', 'Crypto Coins'
        NAIRA_COINS = 'naira_coins', 'Naira Coins'
        BONUS_COINS = 'bonus_coins', 'Bonus Coins'

    class BonusDestination(models.TextChoices):
        CRYPTO = 'crypto', 'Crypto Withdraw Balance'
        NAIRA = 'naira', 'Naira Withdraw Balance'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User', on_delete=models.CASCADE, related_name='spins',
    )
    wheel = models.ForeignKey(
        Wheel, on_delete=models.PROTECT, related_name='spins',
    )

    stake_amount = models.DecimalField(max_digits=20, decimal_places=2)

    segment_landed = models.ForeignKey(
        WheelSegment, on_delete=models.PROTECT, related_name='spins',
    )

    payout_amount = models.DecimalField(max_digits=20, decimal_places=2)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    payout_currency = models.CharField(
        max_length=8,
        choices=[
            ('', 'N/A (loss)'),
            ('NGN', 'Naira'),
            ('USDT', 'USDT'),
        ],
        blank=True,
        default='',
        help_text='Real-world currency credited on a win. Empty for losses.',
    )
    credited_balance = models.CharField(
        max_length=20,
        choices=[
            ('', 'N/A (loss)'),
            ('crypto_withdraw', 'Crypto Withdraw Balance'),
            ('naira_withdraw', 'Naira Withdraw Balance'),
            ('naira_coins', 'Naira Coins (push refund)'),
            ('crypto_coins', 'Crypto Coins (push refund)'),
            ('bonus_coins', 'Bonus Coins (push refund)'),
        ],
        blank=True,
        default='',
        help_text=(
            'Where the payout was credited. For wins: a withdraw balance. '
            'For pushes: the origin coin bucket. Empty for losses.'
        ),
    )
    net_credited = models.DecimalField(
        max_digits=20, decimal_places=6,
        default=Decimal('0'),
        help_text=(
            'Actual amount credited to credited_balance. '
            'For wins/partials on bonus spins, this is the bonus-adjusted net '
            '(gross × bonus_payout_rate × conversion_rate). '
            'For naira/crypto wins, this equals payout_amount (100% rate). '
            'For pushes, this is the stake refunded. '
            'For losses, 0.'
        ),
    )
    # source_wallet = models.CharField(max_length=20,choices=[('deposit_coins', 'Deposit Coins'),('bonus_coins', 'Bonus Coins'),],default='deposit_coins',db_index=True,help_text='Which coin balance funded this spin. Determines payout rules.',)
    source_wallet = models.CharField(max_length=20, choices=SourceWallet.choices, default=SourceWallet.NAIRA_COINS, db_index=True, help_text='Which coin balance funded this spin. Determines payout rules.')
    bonus_destination = models.CharField(
        max_length=10,
        choices=BonusDestination.choices,
        blank=True,
        default='',
        help_text=(
            'Which withdraw balance bonus wins should be credited to. '
            'Required only when source_wallet=bonus_coins. '
            'Empty string for non-bonus spins.'
        ),
    )

    # # Provably fair (stubbed for v1)
    server_seed = models.CharField(max_length=128, blank=True)
    server_seed_hash = models.CharField(max_length=128, blank=True)
    client_seed = models.CharField(max_length=128, blank=True)
    nonce = models.IntegerField(default=0)
    rng_value = models.DecimalField(max_digits=15, decimal_places=10, null=True, blank=True)

    lock_transaction = models.ForeignKey(
        'wallet.Transaction', on_delete=models.PROTECT,
        related_name='spin_locks', null=True, blank=True,
    )
    resolution_transaction = models.ForeignKey(
        'wallet.Transaction', on_delete=models.PROTECT,
        related_name='spin_resolutions', null=True, blank=True,
    )

    reference = models.CharField(max_length=200, unique=True, db_index=True)
    is_welcome_spin = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'spins'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['wheel', '-created_at']),
            models.Index(fields=['outcome']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user'],
                condition=Q(is_welcome_spin=True),
                name='one_welcome_spin_per_user',
            ),
        ]

    def __str__(self):
        return f'Spin({self.user}, {self.wheel.name}, {self.outcome}, ₦{self.payout_amount})'