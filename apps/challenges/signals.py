"""
Challenge signals.

Hooks the ChallengeEngine into all relevant events:
  - Post-spin        → spin_count, spin_streak, welcome, win_streak, total_staked
  - Post-login       → daily_login, login_streak
  - Post-deposit     → deposit, deposit_streak
  - Post-referral    → referral (after referred user makes first deposit)

Each signal is as lightweight as possible — heavy logic lives in the engine.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import Signal, receiver

logger = logging.getLogger(__name__)

# Custom signals for events not covered by post_save
user_logged_in_challenge   = Signal()   # args: user
referral_qualified         = Signal()   # args: referrer, referred_user


# ─── Spin signal ──────────────────────────────────────────────────────────────

def connect_spin_signal():
    """Connect to Spin post_save."""
    try:
        from apps.spin.models import Spin

        @receiver(post_save, sender=Spin, dispatch_uid='challenges_spin_handler')
        def on_spin_completed(sender, instance, created, **kwargs):
            if not created:
                return
            try:
                from apps.challenges.engine import ChallengeEngine
                ChallengeEngine.handle_event(
                    user=instance.user,
                    event='spin',
                    metadata={
                        'stake_amount': str(instance.stake_amount),
                        'outcome': instance.outcome,
                        'payout_amount': str(instance.payout_amount),
                        'spin_id': str(instance.id),
                        'is_welcome_spin': instance.is_welcome_spin,
                    },
                )
            except Exception as e:
                logger.exception('Challenge spin signal error: %s', e)

    except ImportError:
        logger.warning('challenges: spin model not available, skipping signal')


# ─── Deposit signal ───────────────────────────────────────────────────────────

def connect_deposit_signal():
    """Connect to Transaction post_save for deposits."""
    try:
        from apps.wallet.models import Transaction

        @receiver(post_save, sender=Transaction, dispatch_uid='challenges_deposit_handler')
        def on_transaction_completed(sender, instance, created, **kwargs):
            if not created:
                return
            if instance.type != 'deposit' or instance.status != 'completed':
                return
            try:
                from apps.challenges.engine import ChallengeEngine
                ChallengeEngine.handle_event(
                    user=instance.user,
                    event='deposit',
                    metadata={
                        'amount': str(instance.amount),
                        'transaction_id': str(instance.id),
                    },
                )
            except Exception as e:
                logger.exception('Challenge deposit signal error: %s', e)

    except ImportError:
        logger.warning('challenges: wallet model not available, skipping signal')


# ─── Login signal ─────────────────────────────────────────────────────────────

@receiver(user_logged_in_challenge)
def on_user_login(sender, user, **kwargs):
    """
    Call this signal from your auth view after a successful login:

        from apps.challenges.signals import user_logged_in_challenge
        user_logged_in_challenge.send(sender=None, user=request.user)
    """
    try:
        from apps.challenges.engine import ChallengeEngine
        ChallengeEngine.handle_event(
            user=user,
            event='login',
            metadata={'user_id': str(user.id)},
        )
    except Exception as e:
        logger.exception('Challenge login signal error: %s', e)


# ─── Referral signal ──────────────────────────────────────────────────────────

@receiver(referral_qualified)
def on_referral_qualified(sender, referrer, referred_user, **kwargs):
    """
    Call this signal from your referral service after the referred user
    completes their first deposit:

        from apps.challenges.signals import referral_qualified
        referral_qualified.send(sender=None, referrer=referrer, referred_user=user)
    """
    try:
        from apps.challenges.engine import ChallengeEngine
        ChallengeEngine.handle_event(
            user=referrer,
            event='referral',
            metadata={
                'referred_user_id': str(referred_user.id),
            },
        )
    except Exception as e:
        logger.exception('Challenge referral signal error: %s', e)


def connect_all():
    """Connect all signals. Called from ChallengesConfig.ready()."""
    connect_spin_signal()
    connect_deposit_signal()


# Auto-connect on import (signals module is imported in apps.py ready())
connect_all()