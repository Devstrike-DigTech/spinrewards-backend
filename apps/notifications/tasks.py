"""
Notification tasks — send Telegram messages to users via Bot API.
All tasks are async (Celery). Fire-and-forget.
"""
import logging
import requests
from django.conf import settings
from config.celery import app as celery_app

logger = logging.getLogger(__name__)


def _send_message(telegram_id: str, text: str) -> None:
    """Send a Telegram message to a user by telegram_id."""
    try:
        response = requests.post(
            f'https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage',
            json={
                'chat_id': telegram_id,
                'text': text,
                'parse_mode': 'HTML',
            },
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error('Failed to send Telegram notification to %s: %s', telegram_id, e)


@celery_app.task(ignore_result=True)
def notify_deposit_success(telegram_id: str, amount: str) -> None:
    _send_message(
        telegram_id,
        f'✅ <b>Deposit Successful</b>\n\n₦{amount} has been added to your wallet.\n\nTap Play to spin now!',
    )


@celery_app.task(ignore_result=True)
def notify_withdrawal_processing(telegram_id: str, amount: str) -> None:
    _send_message(
        telegram_id,
        f'💸 <b>Withdrawal Processing</b>\n\nYour request of ₦{amount} is being processed.',
    )


@celery_app.task(ignore_result=True)
def notify_withdrawal_complete(telegram_id: str, amount: str) -> None:
    _send_message(
        telegram_id,
        f'✅ <b>Withdrawal Sent</b>\n\n₦{amount} has been sent to your bank account.',
    )


@celery_app.task(ignore_result=True)
def notify_withdrawal_failed(telegram_id: str, amount: str) -> None:
    _send_message(
        telegram_id,
        f'❌ <b>Withdrawal Failed</b>\n\nYour request of ₦{amount} could not be processed.\n\nYour funds have been returned to your wallet.',
    )


@celery_app.task(ignore_result=True)
def notify_kyc_approved(telegram_id: str) -> None:
    _send_message(
        telegram_id,
        '✅ <b>KYC Approved</b>\n\nYour identity has been verified. You can now withdraw your winnings!',
    )


@celery_app.task(ignore_result=True)
def notify_kyc_rejected(telegram_id: str, reason: str) -> None:
    _send_message(
        telegram_id,
        f'❌ <b>KYC Rejected</b>\n\nReason: {reason}\n\nPlease resubmit your information.',
    )


@celery_app.task(ignore_result=True)
def notify_spin_win(telegram_id: str, amount: str) -> None:
    _send_message(
        telegram_id,
        f'🎉 <b>You won ₦{amount}!</b>\n\nKeep spinning to win more.',
    )
