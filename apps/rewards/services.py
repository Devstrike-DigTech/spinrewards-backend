import logging
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone

from common.exceptions import RewardAlreadyClaimedError
from apps.wallet.services import WalletService
from apps.wallet.models import Transaction
from .models import DailyReward

logger = logging.getLogger(__name__)

# Day → reward amount mapping
STREAK_REWARDS = {
    1: Decimal('50.00'),
    2: Decimal('75.00'),
    3: Decimal('100.00'),
    4: Decimal('125.00'),
    5: Decimal('150.00'),
    6: Decimal('200.00'),
    7: Decimal('500.00'),  # 7-day bonus
}
DEFAULT_REWARD = Decimal('50.00')


class RewardsService:

    @staticmethod
    def get_status(user) -> dict:
        last_claim = DailyReward.objects.filter(user=user).order_by('-claimed_at').first()
        now = timezone.now()

        streak_days = 1
        can_claim = True

        if last_claim:
            hours_since = (now - last_claim.claimed_at).total_seconds() / 3600
            if hours_since < 24:
                can_claim = False
            elif hours_since < 48:
                streak_days = last_claim.streak_day + 1
            else:
                streak_days = 1  # Streak broken

        today_reward = STREAK_REWARDS.get(streak_days, DEFAULT_REWARD)

        return {
            'streak_days': streak_days,
            'can_claim': can_claim,
            'today_reward': {'amount': str(today_reward), 'type': 'coin'},
            'last_claimed_at': last_claim.claimed_at if last_claim else None,
        }

    @staticmethod
    def claim(user) -> dict:
        status = RewardsService.get_status(user)

        if not status['can_claim']:
            raise RewardAlreadyClaimedError()

        streak_days = status['streak_days']
        reward_amount = STREAK_REWARDS.get(streak_days, DEFAULT_REWARD)

        tx = WalletService.credit(
            user=user,
            amount=reward_amount,
            balance_type='coin',
            tx_type=Transaction.Type.DAILY_REWARD,
            reference_id=f'daily_{user.id}_{timezone.now().date()}',
            metadata={'streak_day': streak_days},
        )

        DailyReward.objects.create(
            user=user,
            streak_day=streak_days,
            reward_amount=reward_amount,
            reward_type='coin',
            transaction=tx,
        )

        new_balance = WalletService.get_balance(user, 'coin')
        logger.info(
            'DAILY_REWARD_CLAIMED user=%s streak=%s amount=%s',
            user.telegram_id, streak_days, reward_amount,
        )

        return {
            'reward_amount': str(reward_amount),
            'reward_type': 'coin',
            'streak_days': streak_days,
            'new_coin_balance': str(new_balance),
            'message': f'Day {streak_days} reward claimed!',
        }
