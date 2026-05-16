"""
Challenge Engine.

The engine is the single source of truth for all challenge logic:
  - Window calculation (when does a progress window start/end)
  - Progress lookup or creation for a given user + challenge
  - Progress increment with duplicate protection
  - Reward distribution when target is reached
  - Eligibility checks (active, not expired, max completions not exceeded)

Usage:
    from apps.challenges.engine import ChallengeEngine

    # Called from signals after a spin:
    ChallengeEngine.handle_event(user=user, event='spin', metadata={
        'stake_amount': Decimal('1000'),
        'outcome': 'win',
        'spin_id': str(spin.id),
    })

    # Called from signals after a login:
    ChallengeEngine.handle_event(user=user, event='login', metadata={})

    # Called from signals after a deposit:
    ChallengeEngine.handle_event(user=user, event='deposit', metadata={
        'amount': Decimal('5000'),
    })

    # Called from referral service after referred user makes first deposit:
    ChallengeEngine.handle_event(user=referrer, event='referral', metadata={
        'referred_user_id': str(referred_user.id),
    })
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Challenge, ChallengeProgress

logger = logging.getLogger(__name__)

# Events that each challenge type listens to
CHALLENGE_EVENT_MAP = {
    'spin_count':    ['spin'],
    'spin_streak':   ['spin'],
    'daily_login':   ['login'],
    'login_streak':  ['login'],
    'referral':      ['referral'],
    'deposit':       ['deposit'],
    'deposit_streak': ['deposit'],
    'welcome':       ['spin'],
    'win_streak':    ['spin'],
    'total_staked':  ['spin'],
    'custom':        ['spin', 'login', 'deposit', 'referral'],
}


class ChallengeEngine:

    @classmethod
    def handle_event(cls, user, event: str, metadata: dict):
        """
        Main entry point. Called after any relevant event.

        Finds all active challenges that listen to this event type,
        then increments progress for each one.
        """
        now = timezone.now()

        # Find all active challenges that listen to this event
        relevant_types = [
            ctype for ctype, events in CHALLENGE_EVENT_MAP.items()
            if event in events
        ]

        challenges = Challenge.objects.filter(
            type__in=relevant_types,
            is_active=True,
            starts_at__lte=now,
        ).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )

        for challenge in challenges:
            try:
                cls._process_challenge(user, challenge, event, metadata)
            except Exception as e:
                logger.exception(
                    'Challenge engine error: user=%s challenge=%s event=%s err=%s',
                    user.id, challenge.id, event, e,
                )

    @classmethod
    def _process_challenge(cls, user, challenge: Challenge, event: str, metadata: dict):
        """Process a single challenge for a user."""
        # Check eligibility before touching progress
        if not cls._is_eligible(user, challenge, metadata):
            return

        window_start, window_end = cls._get_window(challenge)

        with transaction.atomic():
            progress, created = ChallengeProgress.objects.select_for_update().get_or_create(
                user=user,
                challenge=challenge,
                window_start=window_start,
                defaults={
                    'window_end': window_end,
                    'current_count': 0,
                    'is_completed': False,
                },
            )

            # Don't increment if already completed this window
            # (unless max_completions_per_user allows more)
            if progress.is_completed:
                max_c = challenge.max_completions_per_user
                if max_c is None or progress.completion_number >= max_c:
                    return

            # Increment
            progress.current_count += 1
            progress.save(update_fields=['current_count', 'updated_at'])

            logger.debug(
                'Challenge progress: user=%s challenge=%s count=%d/%d',
                user.id, challenge.id,
                progress.current_count, challenge.target_count,
            )

            # Check if completed
            if progress.current_count >= challenge.target_count:
                cls._mark_completed(progress)
                cls._distribute_reward(user, challenge, progress)

    @classmethod
    def _is_eligible(cls, user, challenge: Challenge, metadata: dict) -> bool:
        """
        Check if the user is eligible for this challenge given the event metadata.
        """
        criteria = challenge.criteria

        # Welcome challenge — only for first-ever spin
        if challenge.type == Challenge.Type.WELCOME:
            from apps.spin.models import Spin
            spin_count = Spin.objects.filter(user=user).count()
            if spin_count > 1:
                return False

        # Deposit challenges — check min_deposit
        if challenge.type in (Challenge.Type.DEPOSIT, Challenge.Type.DEPOSIT_STREAK):
            min_deposit = Decimal(str(criteria.get('min_deposit', '0')))
            amount = Decimal(str(metadata.get('amount', '0')))
            if amount < min_deposit:
                return False

        # Spin challenges — check min_stake
        if challenge.type in (
            Challenge.Type.SPIN_COUNT, Challenge.Type.SPIN_STREAK,
            Challenge.Type.WIN_STREAK, Challenge.Type.TOTAL_STAKED,
        ):
            min_stake = Decimal(str(criteria.get('min_stake', '0')))
            stake = Decimal(str(metadata.get('stake_amount', '0')))
            if stake < min_stake:
                return False

        # Win streak — only count wins
        if challenge.type == Challenge.Type.WIN_STREAK:
            if metadata.get('outcome') != 'win':
                return False

        # One-time challenges — check not already completed ever
        if challenge.recurrence == Challenge.Recurrence.ONE_TIME:
            already = ChallengeProgress.objects.filter(
                user=user, challenge=challenge, is_completed=True,
            ).exists()
            if already:
                return False

        return True

    @classmethod
    def _get_window(cls, challenge: Challenge):
        """
        Calculate the current window start and end for a challenge's recurrence.

        Returns (window_start, window_end).
        """
        now = timezone.now()

        if challenge.recurrence == Challenge.Recurrence.DAILY:
            window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            window_end   = window_start + timedelta(days=1)

        elif challenge.recurrence == Challenge.Recurrence.WEEKLY:
            # Monday-based week
            days_since_monday = now.weekday()
            window_start = (now - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            window_end = window_start + timedelta(weeks=1)

        elif challenge.recurrence == Challenge.Recurrence.MONTHLY:
            window_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            # First day of next month
            if now.month == 12:
                window_end = now.replace(year=now.year + 1, month=1, day=1,
                                         hour=0, minute=0, second=0, microsecond=0)
            else:
                window_end = now.replace(month=now.month + 1, day=1,
                                         hour=0, minute=0, second=0, microsecond=0)

        elif challenge.recurrence in (
            Challenge.Recurrence.ONE_TIME,
            Challenge.Recurrence.PERMANENT,
        ):
            # Single window from challenge start
            window_start = challenge.starts_at
            window_end   = challenge.expires_at  # may be None

        else:
            window_start = challenge.starts_at
            window_end   = None

        return window_start, window_end

    @classmethod
    def _mark_completed(cls, progress: ChallengeProgress):
        """Mark a progress record as completed."""
        progress.is_completed = True
        progress.completed_at = timezone.now()
        progress.save(update_fields=['is_completed', 'completed_at', 'updated_at'])
        logger.info(
            'Challenge completed: user=%s challenge=%s',
            progress.user_id, progress.challenge_id,
        )

    @classmethod
    def _distribute_reward(cls, user, challenge: Challenge, progress: ChallengeProgress):
        """
        Issue the reward to the user based on reward type.

        Coins  → credit coin wallet
        Cash   → credit cash wallet
        Free spins → grant free spin (via spin grants system)
        Multiplier boost → store as user modifier
        """
        reward = challenge.reward
        reward_type   = reward.get('type', 'coins')
        reward_amount = Decimal(str(reward.get('amount', 0)))

        try:
            if reward_type == Challenge.RewardType.COINS:
                from apps.wallet.services import WalletService
                from apps.wallet.models import Transaction
                WalletService.credit(
                    user=user,
                    amount=reward_amount,
                    balance_type='coin',
                    tx_type=Transaction.Type.WIN,
                    reference_id=f'challenge_{progress.id}',
                    metadata={
                        'source': 'challenge_reward',
                        'challenge_id': str(challenge.id),
                        'challenge_name': challenge.name,
                    },
                )
                logger.info(
                    'Challenge reward (coins): user=%s challenge=%s amount=%s',
                    user.id, challenge.name, reward_amount,
                )

            elif reward_type == Challenge.RewardType.CASH:
                from apps.wallet.services import WalletService
                from apps.wallet.models import Transaction
                WalletService.credit(
                    user=user,
                    amount=reward_amount,
                    balance_type='cash',
                    tx_type=Transaction.Type.WIN,
                    reference_id=f'challenge_{progress.id}',
                    metadata={
                        'source': 'challenge_reward',
                        'challenge_id': str(challenge.id),
                        'challenge_name': challenge.name,
                    },
                )
                logger.info(
                    'Challenge reward (cash): user=%s challenge=%s amount=%s',
                    user.id, challenge.name, reward_amount,
                )

            elif reward_type == Challenge.RewardType.FREE_SPINS:
                # Grant free spins — implementation depends on your spin grants model
                # For now log it; wire to SpinGrant when that model exists
                logger.info(
                    'Challenge reward (free_spins): user=%s challenge=%s spins=%s',
                    user.id, challenge.name, reward_amount,
                )
                # TODO: SpinGrant.objects.create(user=user, spins=int(reward_amount), ...)

            elif reward_type == Challenge.RewardType.MULTIPLIER_BOOST:
                # Store as a pending multiplier modifier
                logger.info(
                    'Challenge reward (multiplier_boost): user=%s challenge=%s multiplier=%sx',
                    user.id, challenge.name, reward_amount,
                )
                # TODO: UserModifier.objects.create(user=user, type='multiplier', value=reward_amount)

        except Exception as e:
            logger.exception(
                'Failed to distribute challenge reward: user=%s challenge=%s err=%s',
                user.id, challenge.id, e,
            )

        # Mark reward as claimed
        progress.reward_claimed    = True
        progress.reward_claimed_at = timezone.now()
        progress.save(update_fields=['reward_claimed', 'reward_claimed_at', 'updated_at'])


from django.db import models  # noqa: E402 (needed for Q in handle_event)