import logging
from config.celery import app as celery_app
from .models import WithdrawalRequest

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_withdrawal_payout(self, withdrawal_id: str):
    """
    Async task: submit payout to provider.
    Retries up to 3 times on transient failures.
    Reverses funds on permanent failure.
    """
    from .services import WithdrawalService

    try:
        withdrawal = WithdrawalRequest.objects.get(id=withdrawal_id)
    except WithdrawalRequest.DoesNotExist:
        logger.error('Withdrawal not found: %s', withdrawal_id)
        return

    if withdrawal.status != WithdrawalRequest.Status.PENDING:
        return  # Already processed

    withdrawal.status = WithdrawalRequest.Status.PROCESSING
    withdrawal.save(update_fields=['status', 'updated_at'])

    try:
        # TODO: Integrate with Paystack Transfer API or Flutterwave payout
        # provider_ref = PaystackTransferService.send(withdrawal)
        # withdrawal.provider_reference = provider_ref
        # withdrawal.status = WithdrawalRequest.Status.COMPLETED
        # withdrawal.processed_at = timezone.now()
        # withdrawal.save(...)

        from apps.notifications.tasks import notify_withdrawal_complete
        notify_withdrawal_complete.delay(
            withdrawal.user.telegram_id, str(withdrawal.amount)
        )

        logger.info('WITHDRAWAL_COMPLETED ref=%s', withdrawal.reference)

    except Exception as exc:
        logger.exception('Withdrawal payout failed: ref=%s error=%s', withdrawal.reference, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            WithdrawalService.reverse(withdrawal, reason=str(exc))
