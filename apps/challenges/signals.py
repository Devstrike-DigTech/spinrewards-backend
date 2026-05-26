"""
Challenge signals — FIXED.

The bug: receivers defined inside connect_xxx() functions get garbage-collected
because Django uses weak references by default. Receivers must be at module
scope OR use weak=False.

This version uses top-level receivers — they stay alive as long as the module
is loaded.
"""
import logging

from django.db.models.signals import post_save
from django.dispatch import Signal, receiver

logger = logging.getLogger(__name__)

# Custom signals for events not covered by post_save
user_logged_in_challenge = Signal()   # args: user
referral_qualified       = Signal()   # args: referrer, referred_user


# ─── Lazy imports inside receivers (avoid circular imports) ───────────────────

def _get_spin_model():
    from apps.spin.models import Spin
    return Spin


def _get_transaction_model():
    from apps.wallet.models import Transaction
    return Transaction


def _get_engine():
    from apps.challenges.engine import ChallengeEngine
    return ChallengeEngine


# ─── Spin receiver (top-level so weak refs don't kill it) ─────────────────────

@receiver(post_save, dispatch_uid='challenges_spin_handler')
def on_any_save(sender, instance, created, **kwargs):
    """
    Listens to ALL post_save signals and filters by model name.

    Why this approach: we can't use sender=Spin at decoration time because
    that would require importing the model at module load, which causes
    circular import issues. Instead we filter inside the handler.
    """
    if not created:
        return

    model_name = sender.__name__

    if model_name == 'Spin':
        _handle_spin(instance)
    elif model_name == 'Transaction':
        _handle_transaction(instance)


def _handle_spin(spin):
    try:
        engine = _get_engine()
        engine.handle_event(
            user=spin.user,
            event='spin',
            metadata={
                'stake_amount': str(spin.stake_amount),
                'outcome': spin.outcome,
                'payout_amount': str(spin.payout_amount),
                'spin_id': str(spin.id),
                'is_welcome_spin': getattr(spin, 'is_welcome_spin', False),
            },
        )
    except Exception as e:
        logger.exception('Challenge spin signal error: %s', e)


def _handle_transaction(tx):
    if tx.type != 'deposit' or tx.status != 'completed':
        return
    try:
        engine = _get_engine()
        engine.handle_event(
            user=tx.user,
            event='deposit',
            metadata={
                'amount': str(tx.amount),
                'transaction_id': str(tx.id),
            },
        )
    except Exception as e:
        logger.exception('Challenge deposit signal error: %s', e)


# ─── Login signal (top-level receiver) ────────────────────────────────────────

@receiver(user_logged_in_challenge)
def on_user_login(sender, user, **kwargs):
    """
    Trigger by calling:
        from apps.challenges.signals import user_logged_in_challenge
        user_logged_in_challenge.send(sender=None, user=request.user)
    """
    try:
        engine = _get_engine()
        engine.handle_event(
            user=user,
            event='login',
            metadata={'user_id': str(user.id)},
        )
    except Exception as e:
        logger.exception('Challenge login signal error: %s', e)


# ─── Referral signal (top-level receiver) ─────────────────────────────────────

@receiver(referral_qualified)
def on_referral_qualified(sender, referrer, referred_user, **kwargs):
    """
    Trigger by calling:
        from apps.challenges.signals import referral_qualified
        referral_qualified.send(sender=None, referrer=referrer, referred_user=referred_user)
    """
    try:
        engine = _get_engine()
        engine.handle_event(
            user=referrer,
            event='referral',
            metadata={
                'referred_user_id': str(referred_user.id),
            },
        )
    except Exception as e:
        logger.exception('Challenge referral signal error: %s', e)


logger.info('Challenge signals connected')