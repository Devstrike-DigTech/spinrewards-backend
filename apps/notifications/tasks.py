"""
Celery task for sending notifications asynchronously.

Imported by NotificationService.send_async() — keeps user-facing API
responses fast by pushing the actual HTTP call to a worker.
"""
import logging

from celery import shared_task

from .services import NotificationService

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=10,   # 10 seconds between retries
    autoretry_for=(Exception,),
    retry_backoff=True,        # exponential backoff: 10s, 20s, 40s
    retry_backoff_max=120,
    retry_jitter=True,
)
def send_notification_task(self, telegram_id: str, notification_type: str, data: dict):
    """
    Send a notification in the background.

    Retries up to 3 times with exponential backoff on any exception.
    After all retries fail, logs the error and gives up gracefully.
    """
    success = NotificationService.send(
        telegram_id=telegram_id,
        notification_type=notification_type,
        data=data,
    )

    if not success:
        # send() returns False on non-2xx responses or config issues.
        # Don't retry on these — they're permanent failures (bad config,
        # bad notification type, bot rejected the message). The warning
        # was already logged inside send().
        logger.info(
            'Notification not sent (non-retryable): type=%s telegram_id=%s',
            notification_type, telegram_id,
        )
    else:
        logger.debug(
            'Notification task completed: type=%s telegram_id=%s',
            notification_type, telegram_id,
        )
