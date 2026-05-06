# import logging
# from config.celery import app as celery_app
# from .models import WithdrawalRequest

# logger = logging.getLogger(__name__)


# @celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
# def process_withdrawal_payout(self, withdrawal_id: str):
#     """
#     Async task: submit payout to provider.
#     Retries up to 3 times on transient failures.
#     Reverses funds on permanent failure.
#     """
#     from .services import WithdrawalService

#     try:
#         withdrawal = WithdrawalRequest.objects.get(id=withdrawal_id)
#     except WithdrawalRequest.DoesNotExist:
#         logger.error('Withdrawal not found: %s', withdrawal_id)
#         return

#     if withdrawal.status != WithdrawalRequest.Status.PENDING:
#         return  # Already processed

#     withdrawal.status = WithdrawalRequest.Status.PROCESSING
#     withdrawal.save(update_fields=['status', 'updated_at'])

#     try:
#         # TODO: Integrate with Paystack Transfer API or Flutterwave payout
#         # provider_ref = PaystackTransferService.send(withdrawal)
#         # withdrawal.provider_reference = provider_ref
#         # withdrawal.status = WithdrawalRequest.Status.COMPLETED
#         # withdrawal.processed_at = timezone.now()
#         # withdrawal.save(...)

#         from apps.notifications.tasks import notify_withdrawal_complete
#         notify_withdrawal_complete.delay(
#             withdrawal.user.telegram_id, str(withdrawal.amount)
#         )

#         logger.info('WITHDRAWAL_COMPLETED ref=%s', withdrawal.reference)

#     except Exception as exc:
#         logger.exception('Withdrawal payout failed: ref=%s error=%s', withdrawal.reference, exc)
#         try:
#             self.retry(exc=exc)
#         except self.MaxRetriesExceededError:
#             WithdrawalService.reverse(withdrawal, reason=str(exc))

"""Celery tasks for withdrawals."""
import logging

from celery import shared_task

from .providers.base import PayoutProviderError

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(PayoutProviderError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def process_withdrawal(self, withdrawal_id):
    from .models import Withdrawal
    from .services import WithdrawalService

    try:
        withdrawal = Withdrawal.objects.get(id=withdrawal_id)
    except Withdrawal.DoesNotExist:
        logger.error('process_withdrawal: not found %s', withdrawal_id)
        return

    if withdrawal.status != Withdrawal.Status.PENDING:
        logger.warning(
            'process_withdrawal: skipping non-pending %s status=%s',
            withdrawal_id, withdrawal.status,
        )
        return

    try:
        WithdrawalService.process(withdrawal)
    except PayoutProviderError:
        raise
    except Exception as e:
        logger.exception('Unexpected error processing %s', withdrawal_id)
        WithdrawalService.fail(withdrawal, f'Internal error: {e}')


@shared_task
def reconcile_pending_withdrawals():
    """Periodic task: reconcile stuck withdrawals via provider verify."""
    from datetime import timedelta
    from django.utils import timezone

    from .models import Withdrawal
    from .providers import get_provider
    from .services import WithdrawalService

    cutoff = timezone.now() - timedelta(minutes=5)
    stuck = Withdrawal.objects.filter(
        status=Withdrawal.Status.PROCESSING,
        processing_at__lte=cutoff,
        provider_transfer_id__gt='',
    )

    provider = get_provider()
    count = 0
    for withdrawal in stuck:
        try:
            result = provider.verify_transfer(withdrawal.provider_transfer_id)
            if result.status == 'completed':
                WithdrawalService.complete(withdrawal)
                count += 1
            elif result.status == 'failed':
                WithdrawalService.fail(withdrawal, 'Reconciliation found failed transfer')
                count += 1
        except PayoutProviderError as e:
            logger.warning('Reconciliation failed for %s: %s', withdrawal.id, e)

    if count:
        logger.info('Reconciled %s stuck withdrawals', count)