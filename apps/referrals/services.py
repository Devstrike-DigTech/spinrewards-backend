import logging
from decimal import Decimal
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.wallet.services import WalletService
from apps.wallet.models import Transaction
from .models import Referral

logger = logging.getLogger(__name__)

REFERRER_BONUS = Decimal('500.00')
REFEREE_BONUS = Decimal('200.00')


class ReferralService:

    @staticmethod
    def check_first_deposit(user) -> None:
        """
        Called after every successful deposit.
        Pays out referral bonuses only on the user's very first deposit.
        """
        referral = Referral.objects.filter(
            referred_user=user,
            status=Referral.Status.PENDING,
        ).first()

        if not referral:
            return

        # Ensure it's truly the first deposit
        deposit_count = Transaction.objects.filter(
            user=user,
            type=Transaction.Type.DEPOSIT,
            status=Transaction.Status.COMPLETED,
        ).count()

        if deposit_count != 1:
            return

        with db_transaction.atomic():
            # Credit referrer
            WalletService.credit(
                user=referral.referrer,
                amount=REFERRER_BONUS,
                balance_type='coin',
                tx_type=Transaction.Type.REFERRAL_BONUS,
                reference_id=f'ref_referrer_{referral.id}',
                metadata={'referral_id': str(referral.id)},
            )

            # Credit referee
            WalletService.credit(
                user=user,
                amount=REFEREE_BONUS,
                balance_type='coin',
                tx_type=Transaction.Type.REFERRAL_BONUS,
                reference_id=f'ref_referee_{referral.id}',
                metadata={'referral_id': str(referral.id)},
            )

            referral.status = Referral.Status.CONVERTED
            referral.referrer_bonus_amount = REFERRER_BONUS
            referral.referee_bonus_amount = REFEREE_BONUS
            referral.converted_at = timezone.now()
            referral.save()

        logger.info(
            'REFERRAL_CONVERTED referrer=%s referee=%s',
            referral.referrer.telegram_id, user.telegram_id,
        )
