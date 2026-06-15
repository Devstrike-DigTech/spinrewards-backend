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
from apps.settings_app.models import SettingKey
from apps.settings_app.services import get_setting
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService

from .models import Withdrawal
from .providers import get_provider
from .providers.base import PayoutProviderError
from apps.notifications.services import NotificationService

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
    """Withdrawal-domain error with an optional error code for the API layer."""
    def __init__(self, message, code='WITHDRAWAL_REJECTED'):
        super().__init__(message)
        self.code = code


class WithdrawalService:

    # ── Public entry points ───────────────────────────────────────────────

    @staticmethod
    @db_transaction.atomic
    def request(
        user,
        amount: Decimal,
        rail: str = 'bank',
        bank_account=None,
        wallet_address: str = '',
        network: str = '',
    ) -> Withdrawal:
        """
        Submit a withdrawal request.
    
        Bank rail (NGN):
            - Debits naira_withdraw_balance
            - Auto-processes if amount < AUTO_PAYOUT_THRESHOLD
            - Manual review if amount ≥ threshold
    
        Crypto rail (USDT):
            - Debits crypto_withdraw_balance
            - ALWAYS forced to manual review (per D7)
            - Requires CRYPTO_WITHDRAWAL_ENABLED flag
    
        Args:
            user: User making the request
            amount: amount in the rail's currency (NGN for bank, USDT for crypto)
            rail: 'bank' or 'crypto'
            bank_account: BankAccount instance (bank rail only)
            wallet_address: TRC-20 address (crypto rail only)
            network: 'TRC20' (crypto rail only; defaults to TRC20 if blank)
        """
        if rail == Withdrawal.Rail.CRYPTO:
            # Check feature flag (D7 — Option A: block at API)
            flag = get_setting(SettingKey.CRYPTO_WITHDRAWAL_ENABLED)
            if not (flag > 0):
                raise WithdrawalServiceError(
                    'Crypto withdrawals are not currently available.',
                    code='CRYPTO_WITHDRAWAL_DISABLED',
                )
    
            # Validate crypto-specific fields
            if not wallet_address:
                raise WithdrawalServiceError(
                    'wallet_address is required for crypto withdrawals.',
                    code='MISSING_WALLET_ADDRESS',
                )
    
            # Default network if not provided
            network = network or Withdrawal.Network.TRC20
    
            # Only TRC20 supported in MVP
            if network != Withdrawal.Network.TRC20:
                raise WithdrawalServiceError(
                    f'Network {network!r} not supported. Only TRC20 USDT for now.',
                    code='UNSUPPORTED_NETWORK',
                )
    
            # Validate address format
            from apps.withdrawals.crypto_wallet_service import CryptoWalletService
            CryptoWalletService.validate_address(wallet_address, network)
    
            return WithdrawalService._create_withdrawal(
                user=user,
                amount=amount,
                rail=Withdrawal.Rail.CRYPTO,
                forced_manual_review=True,   # Always review crypto
                wallet_address=wallet_address,
                network=network,
            )
    
        # Bank rail (default)
        return WithdrawalService._create_withdrawal(
            user=user,
            amount=amount,
            rail=Withdrawal.Rail.BANK,
            forced_manual_review=False,
            bank_account=bank_account,
        )
    # def request(user, amount: Decimal, bank_account=None) -> Withdrawal:
        
        # """
        # Standard tiered flow.
        # Auto-processes if amount < AUTO_PAYOUT_THRESHOLD; manual review otherwise.
        # """
        # return WithdrawalService._create_withdrawal(
        #     user=user,
        #     amount=amount,
        #     forced_manual_review=False,
        #     bank_account=bank_account,
        # )

    @staticmethod
    @db_transaction.atomic
    def request_with_manual_review(
        user,
        amount: Decimal,
        rail: str = 'bank',
        bank_account=None,
        wallet_address: str = '',
        network: str = '',
    ) -> Withdrawal:
        """
        Same as request() but always forces manual review.
        Useful for risk-flagged users.
        """
        if rail == Withdrawal.Rail.CRYPTO:
            # ... same crypto flag check + validation as above ...
            # Then:
            return WithdrawalService._create_withdrawal(
                user=user, amount=amount,
                rail=Withdrawal.Rail.CRYPTO,
                forced_manual_review=True,
                wallet_address=wallet_address,
                network=network,
            )
    
        return WithdrawalService._create_withdrawal(
            user=user,
            amount=amount,
            rail=Withdrawal.Rail.BANK,
            forced_manual_review=True,
            bank_account=bank_account,
        )
    # def request_with_manual_review(user, amount: Decimal, bank_account=None) -> Withdrawal:
    #     """
    #     Forced manual review flow.
    #     ALL withdrawals queued for admin approval regardless of amount.
    #     """
    #     return WithdrawalService._create_withdrawal(
    #         user=user,
    #         amount=amount,
    #         forced_manual_review=True,
    #         bank_account=bank_account,
    #     )

    # ── Shared internals ──────────────────────────────────────────────────

    @staticmethod
    def _create_withdrawal(
        user,
        amount: Decimal,
        forced_manual_review: bool,
        rail: str = 'bank',
        bank_account=None,
        wallet_address: str = '',
        network: str = '',
    ) -> Withdrawal:
        """
        Validate, debit appropriate balance, create withdrawal record.
        """
        # Validate
        WithdrawalService._validate_eligibility(user)
        WithdrawalService._validate_amount(amount, rail)
        
    
        # ─── Rail-specific setup ─────────────────────────────────────────
        if rail == Withdrawal.Rail.BANK:
            if bank_account is None:
                bank_account = WithdrawalService._get_active_bank(user)
            if bank_account is None:
                raise WithdrawalServiceError(
                    'No bank account on file. Please add one to withdraw.',
                    code='NO_BANK_ACCOUNT',
                )
    
            source_balance_type = Transaction.BalanceType.NAIRA_WITHDRAW
            currency = Withdrawal.Currency.NGN
            # Tiered: review only if amount ≥ threshold OR explicitly forced
            amount_requires_review = amount >= AUTO_PAYOUT_THRESHOLD()
            requires_review = forced_manual_review or amount_requires_review
    
            balance_label = '₦'
            balance = WalletService.get_balance(user, source_balance_type)
            if balance < amount:
                raise WithdrawalServiceError(
                    f'Insufficient earnings. You have ₦{balance}, '
                    f'requested ₦{amount}.',
                    code='INSUFFICIENT_FUNDS',
                )
    
        elif rail == Withdrawal.Rail.CRYPTO:
            source_balance_type = Transaction.BalanceType.CRYPTO_WITHDRAW
            currency = Withdrawal.Currency.USDT
            # Crypto: ALWAYS review (D7), regardless of amount
            requires_review = True
    
            balance_label = '$'
            balance = WalletService.get_balance(user, source_balance_type)
            if balance < amount:
                raise WithdrawalServiceError(
                    f'Insufficient crypto balance. You have ${balance}, '
                    f'requested ${amount}.',
                    code='INSUFFICIENT_FUNDS',
                )
    
        else:
            raise WithdrawalServiceError(
                f'Unknown rail: {rail!r}',
                code='UNKNOWN_RAIL',
            )
        WithdrawalService._validate_daily_limits(user, amount)
        # ─── Status ──────────────────────────────────────────────────────
        initial_status = (
            Withdrawal.Status.PENDING_REVIEW if requires_review
            else Withdrawal.Status.PENDING
        )
        reference = f'wd_{secrets.token_urlsafe(24)}'
    
        # ─── Create Withdrawal record ────────────────────────────────────
        withdrawal = Withdrawal.objects.create(
            user=user,
            rail=rail,
            currency=currency,
            bank_account=bank_account if rail == Withdrawal.Rail.BANK else None,
            wallet_address=wallet_address if rail == Withdrawal.Rail.CRYPTO else '',
            network=network if rail == Withdrawal.Rail.CRYPTO else '',
            amount=amount,
            fee=Decimal('0.00'),
            net_amount=amount,
            status=initial_status,
            reference=reference,
            requires_review=requires_review,
            forced_manual_review=forced_manual_review,
        )
    
        # ─── Debit source balance ────────────────────────────────────────
        debit_metadata = {
            'withdrawal_id': str(withdrawal.id),
            'rail': rail,
            'currency': currency,
            'forced_manual_review': forced_manual_review,
        }
        if rail == Withdrawal.Rail.BANK:
            debit_metadata['bank_account_id'] = str(bank_account.id)
            debit_metadata['bank'] = bank_account.bank_name
            debit_metadata['account_number_masked'] = f'****{bank_account.account_number[-4:]}'
        else:
            debit_metadata['wallet_address_masked'] = (
                f'{wallet_address[:6]}...{wallet_address[-6:]}'
            )
            debit_metadata['network'] = network
    
        debit_tx = WalletService.debit(
            user=user,
            amount=amount,
            balance_type=source_balance_type,
            tx_type=Transaction.Type.WITHDRAWAL,
            reference_id=reference,
            metadata=debit_metadata,
        )
        withdrawal.debit_transaction = debit_tx
        withdrawal.save(update_fields=['debit_transaction'])
    
        logger.info(
            'Withdrawal created: user=%s rail=%s amount=%s%s status=%s forced=%s',
            user.telegram_id, rail, balance_label, amount,
            initial_status, forced_manual_review,
        )
    
        # ─── Auto-process if eligible (bank only) ────────────────────────
        # Crypto NEVER auto-processes — admin must manually trigger payout.
        if not requires_review and rail == Withdrawal.Rail.BANK:
            withdrawal_id = str(withdrawal.id)
            db_transaction.on_commit(
                lambda: WithdrawalService._enqueue_processing(withdrawal_id)
            )
    
        return withdrawal
    # def _create_withdrawal(user, amount: Decimal, forced_manual_review: bool, bank_account=None,) -> Withdrawal:
    #     """
    #     Validate, debit cash, create withdrawal record.

    #     forced_manual_review=True   → status always pending_review
    #     forced_manual_review=False  → tiered (pending if < threshold, pending_review otherwise)
    #     """
    #     # Validate
    #     WithdrawalService._validate_eligibility(user)
    #     WithdrawalService._validate_amount(amount)
    #     WithdrawalService._validate_daily_limits(user, amount)
    #     # Use passed bank_account; fall back to active default if not provided
    #     if bank_account is None:
    #         bank_account = WithdrawalService._get_active_bank(user)

    #     if bank_account is None:
    #         raise WithdrawalServiceError(
    #             'No bank account on file. Please add one to withdraw.'
    #         )


        # bank_account = WithdrawalService._get_active_bank(user)

        # cash_balance = WalletService.get_balance(user, 'cash')
        # if cash_balance < amount:
        #     raise WithdrawalServiceError(
        #         f'Insufficient cash balance. You have ₦{cash_balance}, '
        #         f'requested ₦{amount}.'
        #     )

        # earnings_balance = WalletService.get_balance(
        #     user, Transaction.BalanceType.NAIRA_WITHDRAW
        # )
        # if earnings_balance < amount:
        #     raise WithdrawalServiceError(
        #         f'Insufficient earnings. You have ₦{earnings_balance}, '
        #         f'requested ₦{amount}.'
        #     )
        # # Determine review state
        # amount_requires_review = amount >= AUTO_PAYOUT_THRESHOLD()
        # requires_review = forced_manual_review or amount_requires_review

        # # Initial status
        # initial_status = (
        #     Withdrawal.Status.PENDING_REVIEW if requires_review
        #     else Withdrawal.Status.PENDING
        # )

        # reference = f'wd_{secrets.token_urlsafe(24)}'

        # withdrawal = Withdrawal.objects.create(
        #     user=user,
        #     bank_account=bank_account,
        #     amount=amount,
        #     fee=Decimal('0.00'),
        #     net_amount=amount,
        #     status=initial_status,
        #     reference=reference,
        #     requires_review=requires_review,
        #     forced_manual_review=forced_manual_review,
        # )

        # # Debit cash
        # debit_tx = WalletService.debit(
        #     user=user,
        #     amount=amount,
        #     balance_type=Transaction.BalanceType.NAIRA_WITHDRAW,
        #     tx_type=Transaction.Type.WITHDRAWAL,
        #     reference_id=reference,
        #     metadata={
        #         'withdrawal_id': str(withdrawal.id),
        #         'bank_account_id': str(bank_account.id),
        #         'bank': bank_account.bank_name,
        #         'account_number_masked': f'****{bank_account.account_number[-4:]}',
        #         'forced_manual_review': forced_manual_review,
        #     },
        # )
        # withdrawal.debit_transaction = debit_tx
        # withdrawal.save(update_fields=['debit_transaction'])

        # logger.info(
        #     'Withdrawal created: user=%s amount=%s status=%s forced_manual=%s',
        #     user.telegram_id, amount, initial_status, forced_manual_review,
        # )

        # # Auto-process if status is pending (under threshold AND not forced)
        # if not requires_review:
        #     withdrawal_id = str(withdrawal.id)
        #     db_transaction.on_commit(
        #         lambda: WithdrawalService._enqueue_processing(withdrawal_id)
        #     )
        #     db_transaction.on_commit(
        #         lambda: _try_notify(
        #             'notify_withdrawal_processing',
        #             user=user, amount=amount,
        #         )
        #     )
        # if withdrawal.status == Withdrawal.Status.PENDING:
        #     NotificationService.send_async(
        #         telegram_id=user.telegram_id,
        #         notification_type='withdrawal_processing',
        #         data={'amount': str(amount)},
        #     )
        # # (If pending_review, don't notify — admin will approve later.
        # #  After approval, fire the same notification from the approve() method.)

        # return withdrawal

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
    # def _validate_amount(amount):
    #     if amount <= 0:
    #         raise WithdrawalServiceError('Amount must be positive.')

    #     if amount < MIN_WITHDRAWAL():
    #         raise WithdrawalServiceError(f'Minimum withdrawal is ₦{MIN_WITHDRAWAL()}.')

    #     if amount > MAX_WITHDRAWAL_PER_TXN():
    #         raise WithdrawalServiceError(f'Maximum per withdrawal is ₦{MAX_WITHDRAWAL_PER_TXN()}.')
    def _validate_amount(amount: Decimal, rail: str = 'bank'):
    # """
    # Validate withdrawal amount against per-rail minimums.
 
    # Bank rail (NGN): MIN_WITHDRAWAL_NGN (default 1000)
    # Crypto rail (USDT): MIN_WITHDRAWAL_USDT (default 5)
    # """
        if amount <= 0:
            raise WithdrawalServiceError(
                'Amount must be greater than zero.',
                code='INVALID_AMOUNT',
            )
    
        if rail == Withdrawal.Rail.BANK:
            min_amount = get_setting(SettingKey.MIN_WITHDRAWAL_NGN)
            if amount < min_amount:
                raise WithdrawalServiceError(
                    f'Minimum NGN withdrawal is ₦{min_amount}.',
                    code='BELOW_MIN_WITHDRAWAL',
                )
        elif rail == Withdrawal.Rail.CRYPTO:
            min_amount = get_setting(SettingKey.MIN_WITHDRAWAL_USDT)
            if amount < min_amount:
                raise WithdrawalServiceError(
                    f'Minimum crypto withdrawal is {min_amount} USDT.',
                    code='BELOW_MIN_WITHDRAWAL',
                )

    @staticmethod
    def _validate_daily_limits(user, amount):
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)

        today = Withdrawal.objects.filter(
            user=user, requested_at__gte=today_start,
        ).exclude(status__in=['cancelled', 'rejected'])

        count = today.count()
        if count >= MAX_DAILY_COUNT():
            raise WithdrawalServiceError(
                f'Daily withdrawal limit reached ({MAX_DAILY_COUNT()} per day).',
                code='DAILY_LIMIT_EXCEEDED',
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
        # withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)

        # if withdrawal.status != Withdrawal.Status.PENDING:
        #     logger.warning(
        #         'process() called on non-pending withdrawal: %s status=%s',
        #         withdrawal.id, withdrawal.status,
        #     )
        #     return withdrawal

        # withdrawal.status = Withdrawal.Status.PROCESSING
        # withdrawal.processing_at = timezone.now()
        # withdrawal.save(update_fields=['status', 'processing_at'])

        # provider = get_provider()
        # bank = withdrawal.bank_account
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
    
        # Dispatch by rail
        if withdrawal.rail == Withdrawal.Rail.BANK:
            return WithdrawalService._process_bank_payout(withdrawal)
        elif withdrawal.rail == Withdrawal.Rail.CRYPTO:
            return WithdrawalService._process_crypto_payout(withdrawal)
        else:
            return WithdrawalService._mark_failed(
                withdrawal, f'Unknown rail: {withdrawal.rail}'
            )

        # # Step 1: Create recipient
        # try:
        #     recipient = provider.create_recipient(
        #         account_number=bank.account_number,
        #         bank_code=bank.bank_code,
        #         account_name=bank.account_name,
        #     )
        #     withdrawal.provider_recipient_id = recipient.recipient_id
        #     withdrawal.save(update_fields=['provider_recipient_id'])
        # except PayoutProviderError as e:
        #     return WithdrawalService._mark_failed(withdrawal, f'Recipient creation failed: {e}')

        # # Step 2: Initiate transfer
        # try:
        #     transfer = provider.initiate_transfer(
        #         recipient_id=recipient.recipient_id,
        #         amount=withdrawal.net_amount,
        #         reference=withdrawal.reference,
        #         reason='Spin Rewards withdrawal',
        #     )
        #     withdrawal.provider_transfer_id = transfer.transfer_id
        #     withdrawal.provider_response = transfer.raw_response
        #     withdrawal.save(update_fields=['provider_transfer_id', 'provider_response'])
        # except PayoutProviderError as e:
        #     return WithdrawalService._mark_failed(withdrawal, f'Transfer initiation failed: {e}')

        # # Step 3: Handle immediate response
        # if transfer.status == 'completed':
        #     return WithdrawalService.complete(withdrawal)
        # elif transfer.status == 'failed':
        #     return WithdrawalService._mark_failed(withdrawal, 'Provider rejected transfer')

        # logger.info('Withdrawal in flight: %s transfer_id=%s', withdrawal.id, transfer.transfer_id)
        # return withdrawal

    # ── Terminal state transitions ────────────────────────────────────────
    def _process_bank_payout(withdrawal):
        """
        Send NGN to user's bank account via the bank provider (Paystack or stub).
        Original v2 flow, unchanged.
        """
        provider = get_provider(Withdrawal.Rail.BANK)
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
            return WithdrawalService._mark_failed(
                withdrawal, f'Recipient creation failed: {e}'
            )
    
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
            withdrawal.save(update_fields=[
                'provider_transfer_id', 'provider_response',
            ])
        except PayoutProviderError as e:
            return WithdrawalService._mark_failed(
                withdrawal, f'Transfer initiation failed: {e}'
            )
    
        # Step 3: Handle immediate response
        if transfer.status == 'completed':
            return WithdrawalService.complete(withdrawal)
        elif transfer.status == 'failed':
            return WithdrawalService._mark_failed(
                withdrawal, 'Provider rejected transfer'
            )
    
        logger.info(
            'Bank withdrawal in flight: %s transfer_id=%s',
            withdrawal.id, transfer.transfer_id,
        )
        return withdrawal

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
        NotificationService.send_async(
            telegram_id=withdrawal.user.telegram_id,
            notification_type='withdrawal_complete',
            data={'amount': str(withdrawal.amount)},
        )

        logger.info('Withdrawal completed: %s ₦%s', withdrawal.id, withdrawal.amount)

        db_transaction.on_commit(
            lambda: _try_notify(
                'notify_withdrawal_complete',
                user=withdrawal.user, amount=withdrawal.amount,
            )
        )

        return withdrawal
    
    @staticmethod
    def _process_crypto_payout(withdrawal):
        """
        Send USDT to user's TRC-20 wallet via NowPayments Mass Payouts.
    
        Flow:
            1. Initiate payout via provider
            2. Store payout_id + tx_hash (may be empty if processing)
            3. If status='completed' → complete() immediately
            4. If status='processing' → wait for webhook
            5. If status='failed' → _mark_failed() + auto-refund
        """
        provider = get_provider(Withdrawal.Rail.CRYPTO)
    
        try:
            payout = provider.initiate_payout(withdrawal)
        except PayoutProviderError as e:
            # Provider rejected at API call → mark failed, refund
            logger.error(
                'Crypto payout failed: withdrawal=%s err=%s',
                withdrawal.id, e,
            )
            return WithdrawalService._mark_failed(
                withdrawal, f'Crypto payout failed: {e}'
            )
    
        # Store provider response
        withdrawal.provider_transfer_id = payout.payout_id
        withdrawal.provider_response = payout.raw_response or {}
        if payout.tx_hash:
            withdrawal.tx_hash = payout.tx_hash
        withdrawal.save(update_fields=[
            'provider_transfer_id', 'provider_response', 'tx_hash',
        ])
    
        # Dispatch by terminal status
        if payout.status == 'completed':
            logger.info(
                'Crypto withdrawal completed instantly: %s tx_hash=%s',
                withdrawal.id, payout.tx_hash[:20] if payout.tx_hash else '(none)',
            )
            return WithdrawalService.complete(withdrawal)
        elif payout.status == 'failed':
            return WithdrawalService._mark_failed(
                withdrawal, 'Provider rejected crypto payout'
            )
    
        # 'processing' — webhook will update later
        logger.info(
            'Crypto withdrawal in flight: %s payout_id=%s',
            withdrawal.id, payout.payout_id,
        )
        return withdrawal

    @staticmethod
    @db_transaction.atomic
    def fail(withdrawal, reason):
        return WithdrawalService._mark_failed(withdrawal, reason)

    # @staticmethod
    # def _mark_failed(withdrawal, reason):
    #     """Internal: mark failed and refund cash. Must be inside atomic."""
    #     withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)

    #     if withdrawal.is_terminal:
    #         return withdrawal

    #     refund_tx = WalletService.credit(
    #         user=withdrawal.user,
    #         amount=withdrawal.amount,
    #         balance_type=Transaction.BalanceType.NAIRA_WITHDRAW,
    #         tx_type=Transaction.Type.REFUND,
    #         reference_id=f'{withdrawal.reference}:refund',
    #         metadata={
    #             'withdrawal_id': str(withdrawal.id),
    #             'reason': reason,
    #         },
    #     )

    #     withdrawal.status = Withdrawal.Status.FAILED
    #     withdrawal.failure_reason = reason[:500]
    #     withdrawal.refund_transaction = refund_tx
    #     withdrawal.save(update_fields=['status', 'failure_reason', 'refund_transaction'])

    #     logger.warning('Withdrawal failed: %s reason=%s', withdrawal.id, reason)

    #     db_transaction.on_commit(
    #         lambda: _try_notify(
    #             'notify_withdrawal_failed',
    #             user=withdrawal.user, amount=withdrawal.amount,
    #         )
    #     )

    #     return withdrawal
    @staticmethod
    @db_transaction.atomic
    def _mark_failed(withdrawal, reason):
        """
        Mark a withdrawal as FAILED and refund the user's withdraw balance.
        Rail-aware: refunds to naira_withdraw (bank) or crypto_withdraw (crypto).
        """
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)
    
        if withdrawal.status == Withdrawal.Status.FAILED:
            return withdrawal
        if withdrawal.is_terminal:
            logger.warning(
                '_mark_failed() on terminal withdrawal: %s status=%s',
                withdrawal.id, withdrawal.status,
            )
            return withdrawal
    
        # Rail-aware refund destination
        if withdrawal.rail == Withdrawal.Rail.CRYPTO:
            refund_balance_type = Transaction.BalanceType.CRYPTO_WITHDRAW
        else:
            refund_balance_type = Transaction.BalanceType.NAIRA_WITHDRAW
    
        # Refund the user's balance
        refund_tx = WalletService.credit(
            user=withdrawal.user,
            amount=withdrawal.amount,
            balance_type=refund_balance_type,
            tx_type=Transaction.Type.REFUND,
            reference_id=f'{withdrawal.reference}:failure_refund',
            metadata={
                'withdrawal_id': str(withdrawal.id),
                'rail': withdrawal.rail,
                'reason': reason[:500],
            },
        )
    
        withdrawal.refund_transaction = refund_tx
        withdrawal.status = Withdrawal.Status.FAILED
        withdrawal.failure_reason = reason[:500]
        withdrawal.completed_at = timezone.now()
        withdrawal.save(update_fields=[
            'refund_transaction', 'status', 'failure_reason', 'completed_at',
        ])
    
        NotificationService.send_async(
            telegram_id=withdrawal.user.telegram_id,
            notification_type='withdrawal_failed',
            data={
                'amount': str(withdrawal.amount),
                'currency': withdrawal.currency,
            },
        )
    
        logger.info(
            'Withdrawal failed: %s rail=%s reason=%s',
            withdrawal.id, withdrawal.rail, reason,
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
        NotificationService.send_async(
            telegram_id=withdrawal.user.telegram_id,
            notification_type='withdrawal_processing',
            data={'amount': str(withdrawal.amount)},
        )

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

    # @staticmethod
    # @db_transaction.atomic
    # def reject(withdrawal, admin_user, reason):
    #     withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)

    #     if withdrawal.status != Withdrawal.Status.PENDING_REVIEW:
    #         raise WithdrawalServiceError(
    #             f'Cannot reject withdrawal in {withdrawal.status} state.'
    #         )

    #     refund_tx = WalletService.credit(
    #         user=withdrawal.user,
    #         amount=withdrawal.amount,
    #         balance_type=Transaction.BalanceType.NAIRA_WITHDRAW,
    #         tx_type=Transaction.Type.REFUND,
    #         reference_id=f'{withdrawal.reference}:reject_refund',
    #         metadata={
    #             'withdrawal_id': str(withdrawal.id),
    #             'rejected_by': admin_user.telegram_id,
    #             'reason': reason,
    #         },
    #     )

    #     withdrawal.status = Withdrawal.Status.REJECTED
    #     withdrawal.reviewed_by = admin_user
    #     withdrawal.reviewed_at = timezone.now()
    #     withdrawal.review_notes = reason[:500]
    #     withdrawal.failure_reason = reason[:500]
    #     withdrawal.refund_transaction = refund_tx
    #     withdrawal.save(update_fields=[
    #         'status', 'reviewed_by', 'reviewed_at', 'review_notes',
    #         'failure_reason', 'refund_transaction',
    #     ])
    #     NotificationService.send_async(
    #         telegram_id=withdrawal.user.telegram_id,
    #         notification_type='withdrawal_rejected',
    #         data={'amount': str(withdrawal.amount)},
    #     )

    #     db_transaction.on_commit(
    #         lambda: _try_notify(
    #             'notify_withdrawal_failed',
    #             user=withdrawal.user, amount=withdrawal.amount,
    #         )
    #     )

    #     logger.info('Withdrawal rejected: %s admin=%s', withdrawal.id, admin_user.telegram_id)
    #     return withdrawal
    @staticmethod
    @db_transaction.atomic
    def reject(withdrawal, admin_user, reason):
        """
        Admin rejects a pending_review withdrawal.
        Refunds the debited amount to the correct withdraw balance based on rail.
        """
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)
    
        if withdrawal.status != Withdrawal.Status.PENDING_REVIEW:
            raise WithdrawalServiceError(
                f'Cannot reject withdrawal in {withdrawal.status} state.',
                code='CANNOT_REJECT',
            )
    
        # ─── Determine which balance to refund to (rail-aware) ──────────
        if withdrawal.rail == Withdrawal.Rail.CRYPTO:
            refund_balance_type = Transaction.BalanceType.CRYPTO_WITHDRAW
        else:
            refund_balance_type = Transaction.BalanceType.NAIRA_WITHDRAW
    
        refund_tx = WalletService.credit(
            user=withdrawal.user,
            amount=withdrawal.amount,
            balance_type=refund_balance_type,
            tx_type=Transaction.Type.REFUND,
            reference_id=f'{withdrawal.reference}:reject_refund',
            metadata={
                'withdrawal_id': str(withdrawal.id),
                'rail': withdrawal.rail,
                'rejected_by': str(admin_user.id),
                'reason': reason[:500],
            },
        )
    
        withdrawal.refund_transaction = refund_tx
        withdrawal.status = Withdrawal.Status.REJECTED
        withdrawal.reviewed_by = admin_user
        withdrawal.reviewed_at = timezone.now()
        withdrawal.review_notes = reason[:500]
        withdrawal.failure_reason = reason[:500]
        withdrawal.save(update_fields=[
            'refund_transaction', 'status', 'reviewed_by', 'reviewed_at',
            'review_notes', 'failure_reason',
        ])
    
        logger.info(
            'Withdrawal rejected: %s rail=%s admin=%s reason=%s',
            withdrawal.id, withdrawal.rail, admin_user.telegram_id, reason,
        )
    
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
            balance_type=Transaction.BalanceType.NAIRA_WITHDRAW,
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
        NotificationService.send_async(
            telegram_id=withdrawal.user.telegram_id,
            notification_type='withdrawal_cancelled',
            data={'amount': str(withdrawal.amount)},
        )

        logger.info('Withdrawal cancelled by user: %s', withdrawal.id)
        return withdrawal
    
    @staticmethod
    @db_transaction.atomic
    def complete_from_payout_webhook(withdrawal, tx_hash: str):
        """
        Mark a crypto withdrawal completed based on payout webhook.
        Stores the on-chain tx_hash.
        """
        withdrawal = Withdrawal.objects.select_for_update().get(id=withdrawal.id)
    
        if withdrawal.status == Withdrawal.Status.COMPLETED:
            # Idempotent — already marked completed
            return withdrawal
    
        if withdrawal.is_terminal:
            logger.warning(
                'complete_from_payout_webhook on terminal withdrawal: %s status=%s',
                withdrawal.id, withdrawal.status,
            )
            return withdrawal
    
        if tx_hash and not withdrawal.tx_hash:
            withdrawal.tx_hash = tx_hash
    
        withdrawal.status = Withdrawal.Status.COMPLETED
        withdrawal.completed_at = timezone.now()
        withdrawal.save(update_fields=['status', 'completed_at', 'tx_hash'])
    
        NotificationService.send_async(
            telegram_id=withdrawal.user.telegram_id,
            notification_type='withdrawal_complete',
            data={
                'amount': str(withdrawal.amount),
                'currency': withdrawal.currency,
            },
        )
    
        logger.info(
            'Crypto withdrawal completed via webhook: %s tx_hash=%s',
            withdrawal.id, tx_hash[:20] if tx_hash else '(none)',
        )
        return withdrawal