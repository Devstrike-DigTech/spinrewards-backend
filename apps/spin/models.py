# import uuid
# from django.db import models


# class RTPTier(models.Model):
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     name = models.CharField(max_length=50)
#     stake_min = models.DecimalField(max_digits=15, decimal_places=2)
#     stake_max = models.DecimalField(max_digits=15, decimal_places=2)
#     # Informational only — not enforced by system
#     rtp_target = models.DecimalField(
#         max_digits=5, decimal_places=2, null=True, blank=True,
#         help_text='Informational RTP target. Not enforced.'
#     )
#     is_active = models.BooleanField(default=True)
#     created_by = models.ForeignKey(
#         'users.User', on_delete=models.SET_NULL, null=True, blank=True,
#         related_name='created_tiers',
#     )
#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         db_table = 'rtp_tiers'
#         ordering = ['stake_min']

#     def __str__(self):
#         return f'{self.name} (₦{self.stake_min} – ₦{self.stake_max})'

#     @property
#     def computed_rtp(self):
#         """Dynamically compute RTP from active outcomes: Σ(probability × multiplier)."""
#         from decimal import Decimal
#         outcomes = self.outcomes.filter(is_active=True)
#         if not outcomes.exists():
#             return Decimal('0')
#         return sum(o.probability * o.multiplier / Decimal('100') for o in outcomes)


# class RTPOutcome(models.Model):
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     tier = models.ForeignKey(
#         RTPTier, on_delete=models.CASCADE, related_name='outcomes'
#     )
#     label = models.CharField(max_length=50, help_text='e.g. Loss, Small Win, Jackpot')
#     multiplier = models.DecimalField(
#         max_digits=10, decimal_places=4,
#         help_text='e.g. 0=loss, 0.5=half back, 2=2x, 20=jackpot'
#     )
#     probability = models.DecimalField(
#         max_digits=7, decimal_places=4,
#         help_text='Percentage probability, e.g. 40.0000 = 40%'
#     )
#     is_active = models.BooleanField(default=True)
#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         db_table = 'rtp_outcomes'
#         ordering = ['multiplier']

#     def __str__(self):
#         return f'{self.label} ({self.multiplier}x @ {self.probability}%)'


# class SpinResult(models.Model):
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     user = models.ForeignKey(
#         'users.User', on_delete=models.CASCADE, related_name='spin_results'
#     )
#     tier = models.ForeignKey(
#         RTPTier, on_delete=models.SET_NULL, null=True, related_name='spin_results'
#     )
#     stake = models.DecimalField(max_digits=15, decimal_places=2)
#     multiplier = models.DecimalField(max_digits=10, decimal_places=4)
#     win_amount = models.DecimalField(max_digits=15, decimal_places=2)
#     outcome_label = models.CharField(max_length=50)

#     # Stored for audit/dispute purposes
#     rng_value = models.DecimalField(max_digits=12, decimal_places=10)

#     stake_transaction = models.OneToOneField(
#         'wallet.Transaction', on_delete=models.CASCADE,
#         related_name='spin_stake', null=True,
#     )
#     win_transaction = models.OneToOneField(
#         'wallet.Transaction', on_delete=models.SET_NULL,
#         related_name='spin_win', null=True, blank=True,
#     )

#     # Client-provided idempotency key
#     idempotency_key = models.CharField(max_length=200, unique=True, db_index=True)

#     created_at = models.DateTimeField(auto_now_add=True)

#     class Meta:
#         db_table = 'spin_results'
#         ordering = ['-created_at']

#     def __str__(self):
#         return f'Spin({self.user}, stake={self.stake}, win={self.win_amount})'

#     @property
#     def result(self):
#         return 'win' if self.win_amount > 0 else 'loss'

"""
Spin engine models.

Architecture:
    Wheel        — a configurable game (Standard, Power, Mega, Welcome, etc.)
    WheelSegment — a slice of a wheel (probability + multiplier)
    Spin         — a single execution of a wheel for a user

Wheel configuration is DB-driven so the admin panel can adjust segments,
probabilities, and multipliers without redeploys. Probability is stored as
INTEGER WEIGHTS (not percentages) — this avoids floating-point edge cases
in selection and keeps math exact.

Provably fair is stubbed but not user-exposed in v1: the server seed is
generated, hashed, and stored. The reveal endpoint comes later.
"""
import uuid
from decimal import Decimal

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

    # Currency the user must stake from. Per PRD:
    #   - Standard / Power / Mega / Daily Challenge: stake from 'coin'
    #   - Welcome (free spin): no stake
    # Wins always credit 'cash' (so they're withdrawable post-KYC).
    currency_type = models.CharField(
        max_length=10, choices=CurrencyType.choices,
        default=CurrencyType.COIN,
    )

    # Stake range. For Welcome wheel, both will be 0.
    min_stake = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    max_stake = models.DecimalField(max_digits=20, decimal_places=2, default=0)

    # Whether new users can use this wheel exactly once for free.
    # Only Welcome wheel sets this True. Enforces uniqueness via Spin's
    # one_welcome_spin_per_user constraint.
    is_welcome_only = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    # Informational. Not enforced — actual RTP is sum(weight × multiplier) / total_weight.
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

    Example wheel with 4 segments:
        Loss (weight 60),
        Small Win (weight 30, mult 2x),
        Medium Win (weight 9, mult 5x),
        Jackpot (weight 1, mult 50x)
    Total weight = 100. Picking randbelow(100) gives a value 0..99,
    mapped to a segment by cumulative range.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wheel = models.ForeignKey(
        Wheel, on_delete=models.CASCADE, related_name='segments',
    )

    # Display order around the wheel (for animation). 0-indexed.
    position = models.IntegerField()

    label = models.CharField(
        max_length=50,
        help_text='User-visible label, e.g. "Loss", "₦1000", "10x"',
    )

    # Multiplier applied to stake on win.
    #   0     = total loss (stake forfeited)
    #   0.5   = half-loss (50% stake returned, 50% forfeited)
    #   1     = push (stake returned, no winnings)
    #   2+    = win (stake returned + multiplied winnings)
    multiplier = models.DecimalField(
        max_digits=10, decimal_places=4,
        help_text='0=loss, 1=push (stake back), 2+=win',
    )

    # Higher weight = more likely. Total per wheel can be any positive integer.
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
            # Probability weight must be > 0 for active segments. (We allow
            # 0 for retired/inactive segments so they're skipped gracefully.)
            models.CheckConstraint(
                check=Q(probability_weight__gte=0),
                name='segment_weight_non_negative',
            ),
            # Multiplier must be non-negative.
            models.CheckConstraint(
                check=Q(multiplier__gte=0),
                name='segment_multiplier_non_negative',
            ),
            # Position must be unique per wheel.
            models.UniqueConstraint(
                fields=['wheel', 'position'],
                name='unique_segment_position_per_wheel',
            ),
        ]

    def __str__(self):
        return f'{self.wheel.name} #{self.position}: {self.label} ({self.multiplier}×, w={self.probability_weight})'


class Spin(models.Model):
    """
    A single spin execution.

    Created atomically with the wallet operations: the row exists if and
    only if the user was charged and the outcome resolved successfully.
    """

    class Outcome(models.TextChoices):
        WIN = 'win', 'Win'
        LOSS = 'loss', 'Loss'
        PUSH = 'push', 'Push'
        PARTIAL_LOSS = 'partial_loss', 'Partial Loss'

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

    # Final amount returned to player. For multiplier=0 → 0. For
    # multiplier=1 → stake. For multiplier=3 → 3×stake. (Note: the wallet
    # service treats this differently — see services.py.)
    payout_amount = models.DecimalField(max_digits=20, decimal_places=2)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)

    # ── Provably fair (stubbed for v1, not yet user-exposed) ─────────
    # The server seed is committed (as a hash) before the spin and could
    # be revealed afterward. Client seed is optional client input. Nonce
    # increments per user per session.
    server_seed = models.CharField(max_length=128, blank=True)
    server_seed_hash = models.CharField(max_length=128, blank=True)
    client_seed = models.CharField(max_length=128, blank=True)
    nonce = models.IntegerField(default=0)
    rng_value = models.DecimalField(max_digits=15, decimal_places=10, null=True, blank=True)

    # ── Wallet linkage (immutable audit trail) ────────────────────────
    # Every Spin links to the wallet transactions it caused. PROTECT here
    # means: a wallet transaction tied to a Spin can never be deleted
    # without removing the Spin first.
    lock_transaction = models.ForeignKey(
        'wallet.Transaction', on_delete=models.PROTECT,
        related_name='spin_locks', null=True, blank=True,
    )
    resolution_transaction = models.ForeignKey(
        'wallet.Transaction', on_delete=models.PROTECT,
        related_name='spin_resolutions', null=True, blank=True,
    )

    # Server-generated reference for wallet idempotency. Each spin gets a
    # fresh, non-guessable token. Never user-supplied.
    reference = models.CharField(max_length=200, unique=True, db_index=True)

    # Welcome spin: enforced unique per user via partial constraint below.
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
            # PRD: "All new accounts receive one free Welcome Spin."
            # Postgres partial unique index: at most one is_welcome_spin=True
            # row per user. Trying to insert a second raises IntegrityError.
            models.UniqueConstraint(
                fields=['user'],
                condition=Q(is_welcome_spin=True),
                name='one_welcome_spin_per_user',
            ),
        ]

    def __str__(self):
        return f'Spin({self.user}, {self.wheel.name}, {self.outcome}, ₦{self.payout_amount})'