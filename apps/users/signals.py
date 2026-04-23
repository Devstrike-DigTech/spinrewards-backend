"""
Signals for the users app.

We use a post_save signal on User to auto-create a Wallet on first insert.
This ensures every path that creates a user — auth flow, admin panel, tests,
future bot-initiated flows, management commands — produces a user with a
wallet. No path can forget.
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User

logger = logging.getLogger(__name__)


@receiver(post_save, sender=User)
def create_user_wallet(sender, instance: User, created: bool, **kwargs):
    """
    On first insert, ensure a Wallet exists for the user.

    Uses get_or_create for idempotency: if the signal fires twice for any
    reason (fixtures, re-save, etc.), we never produce a duplicate wallet.

    The wallet creation runs inside whatever transaction the caller is in.
    If the caller's transaction rolls back, the wallet creation rolls back
    with it — exactly what we want.
    """
    if not created:
        return

    # Defer import to avoid circular dependencies at app-loading time.
    from apps.wallet.models import Wallet

    # Using get_or_create is defensive — it handles the case where the
    # signal fires for an existing user row somehow (re-save, loaddata, etc).
    Wallet.objects.get_or_create(user=instance)
    logger.info('Wallet auto-created for user=%s', instance.telegram_id)
