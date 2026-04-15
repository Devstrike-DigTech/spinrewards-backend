import logging
import requests
from decimal import Decimal
from django.db import transaction as db_transaction
from django.utils import timezone
from django.conf import settings

from common.exceptions import ProviderError, BankVerificationError
from common.utils import verify_paystack_signature, verify_flutterwave_signature
from apps.wallet.services import WalletService
from apps.wallet.models import Transaction
from .models import DepositSession

logger = logging.getLogger(__name__)

PAYSTACK_BASE = 'https://api.paystack.co'


class PaymentService:

    # ── Deposit Initiation ─────────────────────────────────────────────────────

    @staticmethod
    def initiate_deposit(user, amount: Decimal, method: str) -> DepositSession:
        """Create a deposit session and return payment URL."""
        if method == DepositSession.Method.PAYSTACK:
            return PaymentService._initiate_paystack(user, amount)
        elif method == DepositSession.Method.FLUTTERWAVE:
            return PaymentService._initiate_flutterwave(user, amount)
        else:
            raise ProviderError(f'Unsupported payment method: {method}')

    @staticmethod
    def _initiate_paystack(user, amount: Decimal) -> DepositSession:
        import uuid
        reference = f'PSK-{uuid.uuid4().hex[:16].upper()}'
        amount_kobo = int(amount * 100)  # Paystack uses kobo

        try:
            response = requests.post(
                f'{PAYSTACK_BASE}/transaction/initialize',
                json={
                    'amount': amount_kobo,
                    'email': f'{user.telegram_id}@spinrewards.app',
                    'reference': reference,
                    'metadata': {
                        'user_id': str(user.id),
                        'telegram_id': user.telegram_id,
                    },
                    'callback_url': f'{settings.MINI_APP_URL}/deposit/callback',
                },
                headers={
                    'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
                    'Content-Type': 'application/json',
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            logger.error('Paystack initiation failed: %s', e)
            raise ProviderError('Paystack is currently unavailable.')

        if not data.get('status'):
            raise ProviderError(data.get('message', 'Paystack error'))

        session = DepositSession.objects.create(
            user=user,
            amount=amount,
            method=DepositSession.Method.PAYSTACK,
            provider_reference=reference,
            payment_url=data['data']['authorization_url'],
        )
        return session

    @staticmethod
    def _initiate_flutterwave(user, amount: Decimal) -> DepositSession:
        import uuid
        reference = f'FLW-{uuid.uuid4().hex[:16].upper()}'

        try:
            response = requests.post(
                'https://api.flutterwave.com/v3/payments',
                json={
                    'tx_ref': reference,
                    'amount': str(amount),
                    'currency': 'NGN',
                    'redirect_url': f'{settings.MINI_APP_URL}/deposit/callback',
                    'customer': {
                        'email': f'{user.telegram_id}@spinrewards.app',
                        'name': user.display_name,
                    },
                    'meta': {'user_id': str(user.id)},
                },
                headers={
                    'Authorization': f'Bearer {settings.FLUTTERWAVE_SECRET_KEY}',
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            logger.error('Flutterwave initiation failed: %s', e)
            raise ProviderError('Flutterwave is currently unavailable.')

        session = DepositSession.objects.create(
            user=user,
            amount=amount,
            method=DepositSession.Method.FLUTTERWAVE,
            provider_reference=reference,
            payment_url=data['data']['link'],
        )
        return session

    # ── Webhook Handling ───────────────────────────────────────────────────────

    @staticmethod
    @db_transaction.atomic
    def handle_paystack_webhook(payload: dict, payload_bytes: bytes, signature: str) -> None:
        """Handle Paystack webhook. Idempotent."""
        if not verify_paystack_signature(payload_bytes, signature):
            logger.warning('Invalid Paystack webhook signature')
            return

        event = payload.get('event')
        if event != 'charge.success':
            return  # Only care about successful charges

        data = payload.get('data', {})
        reference = data.get('reference', '')

        try:
            session = DepositSession.objects.select_for_update().get(
                provider_reference=reference
            )
        except DepositSession.DoesNotExist:
            logger.warning('No deposit session found for reference: %s', reference)
            return

        if session.status == DepositSession.Status.COMPLETED:
            logger.info('Duplicate webhook ignored: %s', reference)
            return

        if data.get('status') != 'success':
            session.status = DepositSession.Status.FAILED
            session.save(update_fields=['status', 'updated_at'])
            return

        # Credit wallet
        WalletService.credit(
            user=session.user,
            amount=session.amount,
            balance_type='coin',
            tx_type=Transaction.Type.DEPOSIT,
            reference_id=f'deposit_{reference}',
            metadata={'provider': 'paystack', 'reference': reference},
        )

        session.status = DepositSession.Status.COMPLETED
        session.webhook_received_at = timezone.now()
        session.completed_at = timezone.now()
        session.save(update_fields=['status', 'webhook_received_at', 'completed_at', 'updated_at'])

        # Post-deposit hooks (referral, notification)
        from apps.referrals.services import ReferralService
        from apps.notifications.tasks import notify_deposit_success
        ReferralService.check_first_deposit(session.user)
        notify_deposit_success.delay(session.user.telegram_id, str(session.amount))

        logger.info('Deposit completed: user=%s amount=%s', session.user.telegram_id, session.amount)

    @staticmethod
    @db_transaction.atomic
    def handle_flutterwave_webhook(payload: dict, signature: str) -> None:
        """Handle Flutterwave webhook. Idempotent."""
        if not verify_flutterwave_signature(signature):
            logger.warning('Invalid Flutterwave webhook signature')
            return

        event = payload.get('event')
        if event != 'charge.completed':
            return

        data = payload.get('data', {})
        reference = data.get('tx_ref', '')

        try:
            session = DepositSession.objects.select_for_update().get(
                provider_reference=reference
            )
        except DepositSession.DoesNotExist:
            logger.warning('No deposit session for FLW reference: %s', reference)
            return

        if session.status == DepositSession.Status.COMPLETED:
            return  # Already processed

        if data.get('status') != 'successful':
            session.status = DepositSession.Status.FAILED
            session.save(update_fields=['status', 'updated_at'])
            return

        WalletService.credit(
            user=session.user,
            amount=session.amount,
            balance_type='coin',
            tx_type=Transaction.Type.DEPOSIT,
            reference_id=f'deposit_{reference}',
            metadata={'provider': 'flutterwave', 'reference': reference},
        )

        session.status = DepositSession.Status.COMPLETED
        session.webhook_received_at = timezone.now()
        session.completed_at = timezone.now()
        session.save(update_fields=['status', 'webhook_received_at', 'completed_at', 'updated_at'])

        from apps.referrals.services import ReferralService
        from apps.notifications.tasks import notify_deposit_success
        ReferralService.check_first_deposit(session.user)
        notify_deposit_success.delay(session.user.telegram_id, str(session.amount))

    # ── Bank Account Verification ──────────────────────────────────────────────

    @staticmethod
    def resolve_bank_account(account_number: str, bank_code: str) -> str:
        """
        Verify a Nigerian bank account via Paystack Resolve Account.
        Returns account_name on success, raises BankVerificationError on failure.
        """
        try:
            response = requests.get(
                f'{PAYSTACK_BASE}/bank/resolve',
                params={'account_number': account_number, 'bank_code': bank_code},
                headers={'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}'},
                timeout=15,
            )
            data = response.json()
        except requests.RequestException as e:
            logger.error('Bank verification failed: %s', e)
            raise BankVerificationError('Could not reach bank verification service.')

        if not data.get('status'):
            raise BankVerificationError(
                data.get('message', 'Bank account verification failed.')
            )

        return data['data']['account_name']
