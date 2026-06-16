"""
Notification service.

Sends notifications via the Telegram Bot Bridge (the spinrewards-bot service
on Railway). All sends are non-blocking — failures are logged but never
propagate to the caller.

Best practice: call NotificationService.send_async() instead of send()
to push work to a Celery worker. This keeps user-facing API responses fast.

Usage:
    from apps.notifications.services import NotificationService

    # Fire-and-forget via Celery (recommended for API endpoints)
    NotificationService.send_async(
        telegram_id=user.telegram_id,
        notification_type='deposit_success',
        data={'amount': '5000'},
    )

    # Synchronous (use only from background tasks or admin scripts)
    NotificationService.send(
        telegram_id=user.telegram_id,
        notification_type='deposit_success',
        data={'amount': '5000'},
    )

Supported notification types:
    - deposit_success
    - withdrawal_processing
    - withdrawal_complete
    - withdrawal_failed
    - kyc_approved
    - kyc_rejected
    - spin_win
"""
import logging
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


SUPPORTED_NOTIFICATION_TYPES = (
    'deposit_success',
    'withdrawal_processing',
    'withdrawal_complete',
    'withdrawal_failed',
    'withdrawal_cancelled',
    'kyc_approved',
    'kyc_rejected',
    'spin_win',
)


class NotificationService:

    DEFAULT_TIMEOUT = 5  # seconds — fail fast, don't block

    @staticmethod
    def send_async(telegram_id, notification_type: str, data: Optional[dict] = None):
        """
        Queue a notification to be sent in the background via Celery.

        Use this from API endpoints and synchronous code paths to avoid
        blocking on a slow Telegram API call.

        If Celery isn't configured or the task module is missing, falls back
        to a synchronous send (with a warning).
        """
        try:
            from apps.notifications.tasks import send_notification_task
            send_notification_task.delay(
                telegram_id=str(telegram_id),
                notification_type=notification_type,
                data=data or {},
            )
        except Exception as e:
            # Celery not available or task import failed — fall back to sync
            logger.warning(
                'Could not queue notification (falling back to sync): %s', e,
            )
            NotificationService.send(telegram_id, notification_type, data)

    @staticmethod
    def send(telegram_id, notification_type: str, data: Optional[dict] = None) -> bool:
        """
        Send a notification synchronously via the Telegram Bot Bridge.

        Returns True on success, False on any failure. Never raises.
        """
        if notification_type not in SUPPORTED_NOTIFICATION_TYPES:
            logger.warning(
                'Unknown notification type "%s"; skipping send.',
                notification_type,
            )
            return False

        url = getattr(settings, 'NOTIFY_SERVICE_URL', None)
        secret = getattr(settings, 'NOTIFY_SECRET', None)

        if not url or not secret:
            logger.warning(
                'NOTIFY_SERVICE_URL or NOTIFY_SECRET not configured; '
                'skipping notification "%s" for telegram_id=%s.',
                notification_type, telegram_id,
            )
            return False

        endpoint = url.rstrip('/') + '/notify'

        try:
            response = requests.post(
                endpoint,
                json={
                    'type': notification_type,
                    'telegram_id': str(telegram_id),
                    'data': data or {},
                },
                headers={
                    'Authorization': f'Bearer {secret}',
                    'Content-Type': 'application/json',
                },
                timeout=NotificationService.DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            logger.warning(
                'Notification send failed (network): type=%s telegram_id=%s err=%s',
                notification_type, telegram_id, e,
            )
            return False

        if response.status_code >= 400:
            logger.warning(
                'Notification rejected: type=%s telegram_id=%s status=%s body=%s',
                notification_type, telegram_id,
                response.status_code, response.text[:200],
            )
            return False

        logger.info(
            'Notification sent: type=%s telegram_id=%s',
            notification_type, telegram_id,
        )
        return True
