"""
Challenges models.

Challenge         — admin-configured task (spin X times, login streak, etc.)
ChallengeProgress — per-user progress tracking within a recurrence window
"""
import uuid
from decimal import Decimal

from django.db import models
from django.utils import timezone


class Challenge(models.Model):
    """
    A configurable task that rewards players for completing defined actions.

    Admin creates challenges via the admin API. The engine evaluates
    active challenges on every relevant event (spin, login, deposit, referral).
    """

    class Type(models.TextChoices):
        SPIN_COUNT    = 'spin_count',    'Spin Count'
        SPIN_STREAK   = 'spin_streak',   'Spin Streak'
        DAILY_LOGIN   = 'daily_login',   'Daily Login'
        LOGIN_STREAK  = 'login_streak',  'Login Streak'
        REFERRAL      = 'referral',      'Referral'
        DEPOSIT       = 'deposit',       'Deposit'
        DEPOSIT_STREAK = 'deposit_streak', 'Deposit Streak'
        WELCOME       = 'welcome',       'Welcome'
        WIN_STREAK    = 'win_streak',    'Win Streak'
        TOTAL_STAKED  = 'total_staked',  'Total Staked'
        CUSTOM        = 'custom',        'Custom'

    class Recurrence(models.TextChoices):
        ONE_TIME   = 'one_time',   'One Time'
        DAILY      = 'daily',      'Daily'
        WEEKLY     = 'weekly',     'Weekly'
        MONTHLY    = 'monthly',    'Monthly'
        PERMANENT  = 'permanent',  'Permanent'

    class RewardType(models.TextChoices):
        COINS            = 'coins',            'Coins'
        CASH             = 'cash',             'Cash'
        FREE_SPINS       = 'free_spins',       'Free Spins'
        MULTIPLIER_BOOST = 'multiplier_boost', 'Multiplier Boost'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Identity
    name        = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    type        = models.CharField(max_length=30, choices=Type.choices)
    recurrence  = models.CharField(max_length=20, choices=Recurrence.choices)

    # Criteria — stored as JSON
    # {
    #   "action": "spin" | "login" | "referral" | "deposit" | "win",
    #   "target_count": 5,
    #   "per": "day" | "week" | "month" | "ever",
    #   "min_stake": "500.00",      (optional)
    #   "min_deposit": "1000.00",   (optional)
    # }
    criteria = models.JSONField(default=dict)

    # Reward — stored as JSON
    # {
    #   "type": "coins" | "cash" | "free_spins" | "multiplier_boost",
    #   "amount": 200,
    #   "expires_in_hours": 24    (optional)
    # }
    reward = models.JSONField(default=dict)

    # Limits
    max_completions_per_user = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='null = unlimited. 1 = once per window.',
    )

    # Visibility and state
    is_active  = models.BooleanField(default=True)
    is_visible = models.BooleanField(
        default=True,
        help_text='False = backend-triggered only, not shown to players.',
    )

    # Scheduling
    starts_at  = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)

    # Ownership
    created_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='created_challenges',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'challenges'
        ordering = ['-created_at']

    def __str__(self):
        return f'Challenge({self.name}, {self.type}, {self.recurrence})'

    @property
    def is_expired(self) -> bool:
        if self.expires_at and timezone.now() > self.expires_at:
            return True
        return False

    @property
    def is_currently_active(self) -> bool:
        if not self.is_active:
            return False
        if self.is_expired:
            return False
        if timezone.now() < self.starts_at:
            return False
        return True

    @property
    def target_count(self) -> int:
        return self.criteria.get('target_count', 1)

    @property
    def reward_type(self) -> str:
        return self.reward.get('type', 'coins')

    @property
    def reward_amount(self) -> Decimal:
        return Decimal(str(self.reward.get('amount', 0)))

    @property
    def participant_count(self) -> int:
        return self.progress_records.values('user').distinct().count()

    @property
    def completion_count(self) -> int:
        return self.progress_records.filter(is_completed=True).count()


class ChallengeProgress(models.Model):
    """
    Tracks a single user's progress on a challenge within a recurrence window.

    A new record is created each time a recurrence window opens.
    One-time / permanent challenges have a single record per user.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user      = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='challenge_progress',
    )
    challenge = models.ForeignKey(
        Challenge,
        on_delete=models.CASCADE,
        related_name='progress_records',
    )

    # Progress
    current_count = models.PositiveIntegerField(default=0)

    # Window
    window_start = models.DateTimeField()
    window_end   = models.DateTimeField(null=True, blank=True)

    # Completion
    is_completed      = models.BooleanField(default=False)
    completed_at      = models.DateTimeField(null=True, blank=True)
    completion_number = models.PositiveIntegerField(
        default=1,
        help_text='Which completion this is (1st, 2nd, etc.) for challenges with multiple allowed.',
    )

    # Reward
    reward_claimed    = models.BooleanField(default=False)
    reward_claimed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'challenge_progress'
        ordering = ['-window_start']
        # One active progress record per user per challenge per window
        unique_together = [('user', 'challenge', 'window_start')]

    def __str__(self):
        return (
            f'ChallengeProgress('
            f'user={self.user_id}, '
            f'challenge={self.challenge.name}, '
            f'count={self.current_count}/{self.challenge.target_count}'
            f')'
        )

    @property
    def target_count(self) -> int:
        return self.challenge.target_count

    @property
    def progress_pct(self) -> float:
        target = self.target_count
        if not target:
            return 0.0
        return round(min(self.current_count / target, 1.0) * 100, 1)