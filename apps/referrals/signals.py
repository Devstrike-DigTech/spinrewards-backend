"""
Referrals signals.

Connects ReferralService.qualify() to the first deposit event.

The qualification trigger:
  1. User makes a deposit (Transaction post_save)
  2. If it's their first completed deposit → ReferralService.qualify(user)
  3. qualify() marks the referral as qualified + fires referral_qualified challenge signal
  4. Challenge engine increments the referrer's referral challenge progress
  5. Reward distributed automatically

No action if the user has no referral entry.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def connect_deposit_signal():
    try:
        from apps.wallet.models import Transaction

        @receiver(
            post_save,
            sender=Transaction,
            dispatch_uid='referrals_deposit_qualify_handler',
        )
        def on_deposit_completed(sender, instance, created, **kwargs):
            if not created:
                return
            if instance.type != 'deposit' or instance.status != 'completed':
                return

            # Only trigger on first deposit
            deposit_count = Transaction.objects.filter(
                user=instance.user,
                type='deposit',
                status='completed',
            ).count()

            if deposit_count != 1:
                # Not the first deposit
                return

            try:
                from apps.referrals.services import ReferralService
                referral = ReferralService.qualify(instance.user)
                if referral:
                    logger.info(
                        'Referral qualified via first deposit: referred_user=%s referrer=%s',
                        instance.user_id, referral.referrer_id,
                    )
            except Exception as e:
                logger.exception('Referral qualify signal error: %s', e)

    except ImportError:
        logger.warning('referrals: wallet model not available, skipping signal')


connect_deposit_signal()