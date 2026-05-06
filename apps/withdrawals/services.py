# import logging
# from decimal import Decimal
# from django.db import transaction as db_transaction
# from django.utils import timezone

# from common.exceptions import KYCRequiredError, InsufficientFundsError
# from common.utils import generate_reference
# from apps.wallet.services import WalletService
# from apps.wallet.models import Transaction
# from apps.kyc.models import KYC
# from .models import WithdrawalRequest

# logger = logging.getLogger(__name__)


# class WithdrawalService:

#     @staticmethod
#     @db_transaction.atomic
#     def request(
#         user,
#         amount: Decimal,
#         bank_account: str,
#         bank_code: str,
#         account_name: str = '',
#     ) -> WithdrawalRequest:
#         """
#         Create a withdrawal request.
#         Validates KYC and balance, debits wallet, creates pending withdrawal.
#         Actual payout is triggered async via Celery.
#         """
#         # 1. KYC gate
#         kyc = getattr(user, 'kyc', None)
#         if not kyc or kyc.status != KYC.Status.APPROVED:
#             raise KYCRequiredError()

#         # 2. Balance check
#         cash_balance = WalletService.get_balance(user, 'cash')
#         if cash_balance < amount:
#             raise InsufficientFundsError('Insufficient cash balance for withdrawal.')

#         # 3. Generate reference
#         reference = generate_reference('WD')

#         # 4. Debit cash balance
#         tx = WalletService.debit(
#             user=user,
#             amount=amount,
#             balance_type='cash',
#             tx_type=Transaction.Type.WITHDRAWAL,
#             reference_id=reference,
#             metadata={'bank_account': bank_account, 'bank_code': bank_code},
#         )

#         # 5. Create withdrawal record
#         withdrawal = WithdrawalRequest.objects.create(
#             user=user,
#             amount=amount,
#             bank_account=bank_account,
#             bank_code=bank_code,
#             account_name=account_name,
#             reference=reference,
#             status=WithdrawalRequest.Status.PENDING,
#             transaction=tx,
#         )

#         logger.info(
#             'WITHDRAWAL_REQUESTED user=%s amount=%s ref=%s',
#             user.telegram_id, amount, reference,
#         )

#         # 6. Trigger async payout
#         from apps.withdrawals.tasks import process_withdrawal_payout
#         process_withdrawal_payout.delay(str(withdrawal.id))

#         return withdrawal

#     @staticmethod
#     @db_transaction.atomic
#     def reverse(withdrawal: WithdrawalRequest, reason: str) -> None:
#         """Restore funds when a payout fails."""
#         WalletService.credit(
#             user=withdrawal.user,
#             amount=withdrawal.amount,
#             balance_type='cash',
#             tx_type=Transaction.Type.REVERSAL,
#             reference_id=f'reversal_{withdrawal.reference}',
#             metadata={'reason': reason, 'original_ref': withdrawal.reference},
#         )

#         withdrawal.status = WithdrawalRequest.Status.REVERSED
#         withdrawal.failure_reason = reason
#         withdrawal.processed_at = timezone.now()
#         withdrawal.save(update_fields=['status', 'failure_reason', 'processed_at', 'updated_at'])

#         from apps.notifications.tasks import notify_withdrawal_failed
#         notify_withdrawal_failed.delay(
#             withdrawal.user.telegram_id, str(withdrawal.amount)
#         )

#         logger.info(
#             'WITHDRAWAL_REVERSED ref=%s reason=%s', withdrawal.reference, reason
#         )
"""
Withdrawal service.

TWO REQUEST METHODS:
  request(user, amount)
    → Tiered flow: auto-process if < AUTO_PAYOUT_THRESHOLD, manual review otherwise.

  request_with_manual_review(user, amount)
    → All withdrawals go through admin review regardless of amount.

Both share the same validation, model, and downstream processing.
The only difference is whether tiered logic applies on entry.
"""
import logging
import secrets
from decimal import Decimal

from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from apps.kyc.models import BankAccount, KYCProfile
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService

from .models import Withdrawal
from .providers import get_provider
from .providers.base import PayoutProviderError

logger = logging.getLogger(__name__)


# ─── Settings helpers ──────────────────────────────────────────────────────

def _setting(name, default):
    return getattr(settings, name, default)


def MIN_WITHDRAWAL():
    return Decimal(str(_setting('WITHDRAWAL_MIN_AMOUNT', '1000.00')))


def MAX_WITHDRAWAL_PER_TXN():
    return Decimal(str(_setting('WITHDRAWAL_MAX_PER_TXN', '100000.00')))


def MAX_DAILY_WITHDRAWAL():
    return Decimal(str(_setting('WITHDRAWAL_MAX_DAILY', '100000.00')))


def MAX_DAILY_COUNT():
    return _setting('WITHDRAWAL_MAX_DAILY_COUNT', 3)


def AUTO_PAYOUT_THRESHOLD():
    return Decimal(str(_setting('WITHDRAWAL_AUTO_THRESHOLD', '10000.00')))


# ─── Notifications (soft import) ───────────────────────────────────────────

def _try_notify(method_name, *args, **kwargs):
    try:
        from apps.notifications.services import NotificationService
        method = getattr(NotificationService, method_name)
        method(*args, **kwargs)
    except ImportError:
        logger.debug('Notifications unavailable; skipping %s', method_name)
    except Exception as e:
        logger.warning('Notification %s failed: %s', method_name, e)


# ─── Service ───────────────────────────────────────────────────────────────

class WithdrawalServiceError(Exception):
    pass


class WithdrawalService:

    # ── Public entry points ───────────────────────────────────────────────

    @staticmethod
    @db_transaction.atomic
    def request(user, amount: Decimal) -> Withdrawal:
        """
        Standard tiered flow.
        Auto-processes if amount < AUTO_PAYOUT_THRESHOLD; manual review otherwise.
        """
        return WithdrawalService._create_withdrawal(
            user=user,
            amount=amount,
            forced_manual_review=False,
        )

    @staticmethod
    @db_transaction.atomic
    def request_with_manual_review(user, amount: Decimal) -> Withdrawal:
        """
        Forced manual review flow.
        ALL withdrawals queued for admin approval regardless of amount.
        """
        return WithdrawalService._create_withdrawal(
            user=user,
            amount=amount,
            forced_manual_review=True,
        )

    # ── Shared internals ──────────────────────────────────────────────────

    @staticmethod
    def _create_withdrawal(user, amount: Decimal, forced_manual_review: bool) -> Withdrawal:
        """
        Validate, debit cash, create withdrawal record.

        forced_manual_review=True   → status always pending_review
        forced_manual_review=False  → tiered (pending if < threshold, pending_review otherwise)
        """
        # Validate
        WithdrawalService._validate_eligibility(user)
        WithdrawalService._validate_amount(amount)
        WithdrawalService._validate_daily_limits(user, amount)

        bank_account = WithdrawalService._get_active_bank(user)

        cash_balance = WalletService.get_balance(user, 'cash')
        if cash_balance < amount:
            raise WithdrawalServiceError(
                f'Insufficient cash balance. You have ₦{cash_balance}, '
                f'requested ₦{amount}.'
            )

        # Determine review state
        amount_requires_review = amount >= AUTO_PAYOUT_THRESHOLD()
        requires_review = forced_manual_review or amount_requires_review

        # Initial status
        initial_status = (
            Withdrawal.Status.PENDING_REVIEW if requires_review
            else Withdrawal.Status.PENDING
        )

        reference = f'wd_{secrets.token_urlsafe(24)}'

        withdrawal = Withdrawal.objects.create(
            user=user,
            bank_account=bank_account,
            amount=amount,
            fee=Decimal('0.00'),
            net_amount=amount,
            status=initial_status,
            reference=reference,
            requires_review=requires_review,
            forced_manual_review=forced_manual_review,
        )

        # Debit cash
        debit_tx = WalletService.debit(
            user=user,
            amount=amount,
            balance_type='cash',
            tx_type=Transaction.Type.WITHDRAWAL,
            reference_id=reference,
            metadata={
                'withdrawal_id': str(withdrawal.id),
                'bank': bank_account.bank_name,
                'account_number_masked': f'****{bank_account.account_number[-4:]}',
                'forced_manual_review': forced_manual_review,
            },
        )
        withdrawal.debit_transaction = debit_tx
        withdrawal.save(update_fields=['debit_transaction'])

        logger.info(
            'Withdrawal created: user=%s amount=%s status=%s forced_manual=%s',
            user.telegram_id, amount, initial_status, forced_manual_review,
        )

        # Auto-process if status is pending (under threshold AND not forced)
        if not requires_review:
            withdrawal_id = str(withdrawal.id)
            db_transaction.on_commit(
                lambda: WithdrawalService._enqueue_processing(withdrawal_id)
            )
            db_transaction.on_commit(
                lambda: _try_notify(
                    'notify_withdrawal_processing',
                    user=user, amount=amount,
                )
            )

        return withdrawal

    @staticmethod
    def _validate_eligibility(user):
        try:
            kyc = user.kyc_profile
        except KYCProfile.DoesNotExist:
            raise WithdrawalServiceError('KYC verification required before withdrawal.')

        if not kyc.can_withdraw:
            raise WithdrawalServiceError(
                'Your KYC verification is not yet approved. '
                'Please complete verification first.'
            )

    @staticmethod
    def _validate_amount(amount):
        if amount <= 0:
            raise WithdrawalServiceError('Amount must be positive.')

        if amount < MIN_WITHDRAWAL():
            raise WithdrawalServiceError(f'Minimum withdrawal is ₦{MIN_WITHDRAWAL()}.')

        if amount > MAX_WITHDRAWAL_PER_TXN():
            raise WithdrawalServiceError(f'Maximum per withdrawal is ₦{MAX_WITHDRAWAL_PER_TXN()}.')

    @staticmethod
    def _validate_daily_limits(user, amount):
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

        today = Withdrawal.objects.filter(
            user=user, requested_at__gte=today_start,
        ).exclude(status__in=['cancelled', 'rejected'])

        count = today.count()
        if count >= MAX_DAILY_COUNT():
            raise WithdrawalServiceError(
                f'Daily withdrawal limit reached ({MAX_DAILY_COUNT()} per day).'
            )

        total = sum((w.amount for w in today), start=Decimal('0'))
        if total + amount > MAX_DAILY_WITHDRAWAL():
            remaining = MAX_DAILY_WITHDRAWAL() - total
            raise WithdrawalServiceError(
                f'Daily total would exceed ₦{MAX_DAILY_WITHDRAWAL()}. '
                f'You have ₦{remaining} remaining today.'
            )

    @staticmethod
    def _get_active_bank(user):
        bank = BankAccount.objects.filter(user=user, is_active=True).first()
        if not bank:
            raise WithdrawalServiceError(
                'No active bank account on file. Complete KYC first.'
            )
        return bank

    # ── Async processing ──────────────────────────────────────────────────

    @staticmethod
    def _enqueue_processing(withdrawal_id):
        try:
            from .tasks import process_withdrawal
            process_withdrawal.delay(withdrawal_id)
        except ImportError:
            logger.warning('Celery unavailable; processing synchronously')
            withdrawal = Withdrawal.objects.get(id=withdrawal_id)
            WithdrawalService.process(withdrawal)

    @staticmethod
    @db_transaction.atomic
    def process(withdrawal):
        """Fire payout via provider. Called by Celery after pending state."""
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)

        if withdrawal.status != Withdrawal.Status.PENDING:
            logger.warning(
                'process() called on non-pending withdrawal: %s status=%s',
                withdrawal.id, withdrawal.status,
            )
            return withdrawal

        withdrawal.status = Withdrawal.Status.PROCESSING
        withdrawal.processing_at = timezone.now()
        withdrawal.save(update_fields=['status', 'processing_at'])

        provider = get_provider()
        bank = withdrawal.bank_account

        # Step 1: Create recipient
        try:
            recipient = provider.create_recipient(
                account_number=bank.account_number,
                bank_code=bank.bank_code,
                account_name=bank.account_name,
            )
            withdrawal.provider_recipient_id = recipient.recipient_id
            withdrawal.save(update_fields=['provider_recipient_id'])
        except PayoutProviderError as e:
            return WithdrawalService._mark_failed(withdrawal, f'Recipient creation failed: {e}')

        # Step 2: Initiate transfer
        try:
            transfer = provider.initiate_transfer(
                recipient_id=recipient.recipient_id,
                amount=withdrawal.net_amount,
                reference=withdrawal.reference,
                reason='Spin Rewards withdrawal',
            )
            withdrawal.provider_transfer_id = transfer.transfer_id
            withdrawal.provider_response = transfer.raw_response
            withdrawal.save(update_fields=['provider_transfer_id', 'provider_response'])
        except PayoutProviderError as e:
            return WithdrawalService._mark_failed(withdrawal, f'Transfer initiation failed: {e}')

        # Step 3: Handle immediate response
        if transfer.status == 'completed':
            return WithdrawalService.complete(withdrawal)
        elif transfer.status == 'failed':
            return WithdrawalService._mark_failed(withdrawal, 'Provider rejected transfer')

        logger.info('Withdrawal in flight: %s transfer_id=%s', withdrawal.id, transfer.transfer_id)
        return withdrawal

    # ── Terminal state transitions ────────────────────────────────────────

    @staticmethod
    @db_transaction.atomic
    def complete(withdrawal):
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)

        if withdrawal.status == Withdrawal.Status.COMPLETED:
            return withdrawal

        if withdrawal.is_terminal:
            logger.warning(
                'complete() on terminal withdrawal: %s status=%s',
                withdrawal.id, withdrawal.status,
            )
            return withdrawal

        withdrawal.status = Withdrawal.Status.COMPLETED
        withdrawal.completed_at = timezone.now()
        withdrawal.save(update_fields=['status', 'completed_at'])

        logger.info('Withdrawal completed: %s ₦%s', withdrawal.id, withdrawal.amount)

        db_transaction.on_commit(
            lambda: _try_notify(
                'notify_withdrawal_complete',
                user=withdrawal.user, amount=withdrawal.amount,
            )
        )

        return withdrawal

    @staticmethod
    @db_transaction.atomic
    def fail(withdrawal, reason):
        return WithdrawalService._mark_failed(withdrawal, reason)

    @staticmethod
    def _mark_failed(withdrawal, reason):
        """Internal: mark failed and refund cash. Must be inside atomic."""
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)

        if withdrawal.is_terminal:
            return withdrawal

        refund_tx = WalletService.credit(
            user=withdrawal.user,
            amount=withdrawal.amount,
            balance_type='cash',
            tx_type=Transaction.Type.REFUND,
            reference_id=f'{withdrawal.reference}:refund',
            metadata={
                'withdrawal_id': str(withdrawal.id),
                'reason': reason,
            },
        )

        withdrawal.status = Withdrawal.Status.FAILED
        withdrawal.failure_reason = reason[:500]
        withdrawal.refund_transaction = refund_tx
        withdrawal.save(update_fields=['status', 'failure_reason', 'refund_transaction'])

        logger.warning('Withdrawal failed: %s reason=%s', withdrawal.id, reason)

        db_transaction.on_commit(
            lambda: _try_notify(
                'notify_withdrawal_failed',
                user=withdrawal.user, amount=withdrawal.amount,
            )
        )

        return withdrawal

    # ── Admin approve/reject ──────────────────────────────────────────────

    @staticmethod
    @db_transaction.atomic
    def approve(withdrawal, admin_user, notes=''):
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)

        if withdrawal.status != Withdrawal.Status.PENDING_REVIEW:
            raise WithdrawalServiceError(
                f'Cannot approve withdrawal in {withdrawal.status} state.'
            )

        withdrawal.status = Withdrawal.Status.PENDING
        withdrawal.reviewed_by = admin_user
        withdrawal.reviewed_at = timezone.now()
        withdrawal.review_notes = notes[:500]
        withdrawal.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'review_notes',
        ])

        withdrawal_id = str(withdrawal.id)
        db_transaction.on_commit(
            lambda: WithdrawalService._enqueue_processing(withdrawal_id)
        )
        db_transaction.on_commit(
            lambda: _try_notify(
                'notify_withdrawal_processing',
                user=withdrawal.user, amount=withdrawal.amount,
            )
        )

        logger.info('Withdrawal approved: %s admin=%s', withdrawal.id, admin_user.telegram_id)
        return withdrawal

    @staticmethod
    @db_transaction.atomic
    def reject(withdrawal, admin_user, reason):
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)

        if withdrawal.status != Withdrawal.Status.PENDING_REVIEW:
            raise WithdrawalServiceError(
                f'Cannot reject withdrawal in {withdrawal.status} state.'
            )

        refund_tx = WalletService.credit(
            user=withdrawal.user,
            amount=withdrawal.amount,
            balance_type='cash',
            tx_type=Transaction.Type.REFUND,
            reference_id=f'{withdrawal.reference}:reject_refund',
            metadata={
                'withdrawal_id': str(withdrawal.id),
                'rejected_by': admin_user.telegram_id,
                'reason': reason,
            },
        )

        withdrawal.status = Withdrawal.Status.REJECTED
        withdrawal.reviewed_by = admin_user
        withdrawal.reviewed_at = timezone.now()
        withdrawal.review_notes = reason[:500]
        withdrawal.failure_reason = reason[:500]
        withdrawal.refund_transaction = refund_tx
        withdrawal.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'review_notes',
            'failure_reason', 'refund_transaction',
        ])

        db_transaction.on_commit(
            lambda: _try_notify(
                'notify_withdrawal_failed',
                user=withdrawal.user, amount=withdrawal.amount,
            )
        )

        logger.info('Withdrawal rejected: %s admin=%s', withdrawal.id, admin_user.telegram_id)
        return withdrawal

    # ── User cancellation ─────────────────────────────────────────────────

    @staticmethod
    @db_transaction.atomic
    def cancel(user, withdrawal):
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)

        if withdrawal.user != user:
            raise WithdrawalServiceError('Not your withdrawal.')

        if withdrawal.status != Withdrawal.Status.PENDING_REVIEW:
            raise WithdrawalServiceError(
                f'Cannot cancel withdrawal in {withdrawal.status} state. '
                'Only pending_review withdrawals can be cancelled.'
            )

        refund_tx = WalletService.credit(
            user=user,
            amount=withdrawal.amount,
            balance_type='cash',
            tx_type=Transaction.Type.REFUND,
            reference_id=f'{withdrawal.reference}:cancel_refund',
            metadata={
                'withdrawal_id': str(withdrawal.id),
                'cancelled_by_user': True,
            },
        )

        withdrawal.status = Withdrawal.Status.CANCELLED
        withdrawal.refund_transaction = refund_tx
        withdrawal.save(update_fields=['status', 'refund_transaction'])

        logger.info('Withdrawal cancelled by user: %s', withdrawal.id)
        return withdrawal