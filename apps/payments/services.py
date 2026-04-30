# import logging
# import requests
# from decimal import Decimal
# from django.db import transaction as db_transaction
# from django.utils import timezone
# from django.conf import settings

# from common.exceptions import ProviderError, BankVerificationError
# from common.utils import verify_paystack_signature, verify_flutterwave_signature
# from apps.wallet.services import WalletService
# from apps.wallet.models import Transaction
# from .models import DepositSession

# logger = logging.getLogger(__name__)

# PAYSTACK_BASE = 'https://api.paystack.co'


# class PaymentService:

#     # ── Deposit Initiation ─────────────────────────────────────────────────────

#     @staticmethod
#     def initiate_deposit(user, amount: Decimal, method: str) -> DepositSession:
#         """Create a deposit session and return payment URL."""
#         if method == DepositSession.Method.PAYSTACK:
#             return PaymentService._initiate_paystack(user, amount)
#         elif method == DepositSession.Method.FLUTTERWAVE:
#             return PaymentService._initiate_flutterwave(user, amount)
#         else:
#             raise ProviderError(f'Unsupported payment method: {method}')

#     @staticmethod
#     def _initiate_paystack(user, amount: Decimal) -> DepositSession:
#         import uuid
#         reference = f'PSK-{uuid.uuid4().hex[:16].upper()}'
#         amount_kobo = int(amount * 100)  # Paystack uses kobo

#         try:
#             response = requests.post(
#                 f'{PAYSTACK_BASE}/transaction/initialize',
#                 json={
#                     'amount': amount_kobo,
#                     'email': f'{user.telegram_id}@spinrewards.app',
#                     'reference': reference,
#                     'metadata': {
#                         'user_id': str(user.id),
#                         'telegram_id': user.telegram_id,
#                     },
#                     'callback_url': f'{settings.MINI_APP_URL}/deposit/callback',
#                 },
#                 headers={
#                     'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
#                     'Content-Type': 'application/json',
#                 },
#                 timeout=30,
#             )
#             response.raise_for_status()
#             data = response.json()
#         except requests.RequestException as e:
#             logger.error('Paystack initiation failed: %s', e)
#             raise ProviderError('Paystack is currently unavailable.')

#         if not data.get('status'):
#             raise ProviderError(data.get('message', 'Paystack error'))

#         session = DepositSession.objects.create(
#             user=user,
#             amount=amount,
#             method=DepositSession.Method.PAYSTACK,
#             provider_reference=reference,
#             payment_url=data['data']['authorization_url'],
#         )
#         return session

#     @staticmethod
#     def _initiate_flutterwave(user, amount: Decimal) -> DepositSession:
#         import uuid
#         reference = f'FLW-{uuid.uuid4().hex[:16].upper()}'

#         try:
#             response = requests.post(
#                 'https://api.flutterwave.com/v3/payments',
#                 json={
#                     'tx_ref': reference,
#                     'amount': str(amount),
#                     'currency': 'NGN',
#                     'redirect_url': f'{settings.MINI_APP_URL}/deposit/callback',
#                     'customer': {
#                         'email': f'{user.telegram_id}@spinrewards.app',
#                         'name': user.display_name,
#                     },
#                     'meta': {'user_id': str(user.id)},
#                 },
#                 headers={
#                     'Authorization': f'Bearer {settings.FLUTTERWAVE_SECRET_KEY}',
#                 },
#                 timeout=30,
#             )
#             response.raise_for_status()
#             data = response.json()
#         except requests.RequestException as e:
#             logger.error('Flutterwave initiation failed: %s', e)
#             raise ProviderError('Flutterwave is currently unavailable.')

#         session = DepositSession.objects.create(
#             user=user,
#             amount=amount,
#             method=DepositSession.Method.FLUTTERWAVE,
#             provider_reference=reference,
#             payment_url=data['data']['link'],
#         )
#         return session

#     # ── Webhook Handling ───────────────────────────────────────────────────────

#     @staticmethod
#     @db_transaction.atomic
#     def handle_paystack_webhook(payload: dict, payload_bytes: bytes, signature: str) -> None:
#         """Handle Paystack webhook. Idempotent."""
#         if not verify_paystack_signature(payload_bytes, signature):
#             logger.warning('Invalid Paystack webhook signature')
#             return

#         event = payload.get('event')
#         if event != 'charge.success':
#             return  # Only care about successful charges

#         data = payload.get('data', {})
#         reference = data.get('reference', '')

#         try:
#             session = DepositSession.objects.select_for_update().get(
#                 provider_reference=reference
#             )
#         except DepositSession.DoesNotExist:
#             logger.warning('No deposit session found for reference: %s', reference)
#             return

#         if session.status == DepositSession.Status.COMPLETED:
#             logger.info('Duplicate webhook ignored: %s', reference)
#             return

#         if data.get('status') != 'success':
#             session.status = DepositSession.Status.FAILED
#             session.save(update_fields=['status', 'updated_at'])
#             return

#         # Credit wallet
#         WalletService.credit(
#             user=session.user,
#             amount=session.amount,
#             balance_type='coin',
#             tx_type=Transaction.Type.DEPOSIT,
#             reference_id=f'deposit_{reference}',
#             metadata={'provider': 'paystack', 'reference': reference},
#         )

#         session.status = DepositSession.Status.COMPLETED
#         session.webhook_received_at = timezone.now()
#         session.completed_at = timezone.now()
#         session.save(update_fields=['status', 'webhook_received_at', 'completed_at', 'updated_at'])

#         # Post-deposit hooks (referral, notification)
#         from apps.referrals.services import ReferralService
#         from apps.notifications.tasks import notify_deposit_success
#         ReferralService.check_first_deposit(session.user)
#         notify_deposit_success.delay(session.user.telegram_id, str(session.amount))

#         logger.info('Deposit completed: user=%s amount=%s', session.user.telegram_id, session.amount)

#     @staticmethod
#     @db_transaction.atomic
#     def handle_flutterwave_webhook(payload: dict, signature: str) -> None:
#         """Handle Flutterwave webhook. Idempotent."""
#         if not verify_flutterwave_signature(signature):
#             logger.warning('Invalid Flutterwave webhook signature')
#             return

#         event = payload.get('event')
#         if event != 'charge.completed':
#             return

#         data = payload.get('data', {})
#         reference = data.get('tx_ref', '')

#         try:
#             session = DepositSession.objects.select_for_update().get(
#                 provider_reference=reference
#             )
#         except DepositSession.DoesNotExist:
#             logger.warning('No deposit session for FLW reference: %s', reference)
#             return

#         if session.status == DepositSession.Status.COMPLETED:
#             return  # Already processed

#         if data.get('status') != 'successful':
#             session.status = DepositSession.Status.FAILED
#             session.save(update_fields=['status', 'updated_at'])
#             return

#         WalletService.credit(
#             user=session.user,
#             amount=session.amount,
#             balance_type='coin',
#             tx_type=Transaction.Type.DEPOSIT,
#             reference_id=f'deposit_{reference}',
#             metadata={'provider': 'flutterwave', 'reference': reference},
#         )

#         session.status = DepositSession.Status.COMPLETED
#         session.webhook_received_at = timezone.now()
#         session.completed_at = timezone.now()
#         session.save(update_fields=['status', 'webhook_received_at', 'completed_at', 'updated_at'])

#         from apps.referrals.services import ReferralService
#         from apps.notifications.tasks import notify_deposit_success
#         ReferralService.check_first_deposit(session.user)
#         notify_deposit_success.delay(session.user.telegram_id, str(session.amount))

#     # ── Bank Account Verification ──────────────────────────────────────────────

#     @staticmethod
#     def resolve_bank_account(account_number: str, bank_code: str) -> str:
#         """
#         Verify a Nigerian bank account via Paystack Resolve Account.
#         Returns account_name on success, raises BankVerificationError on failure.
#         """
#         try:
#             response = requests.get(
#                 f'{PAYSTACK_BASE}/bank/resolve',
#                 params={'account_number': account_number, 'bank_code': bank_code},
#                 headers={'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}'},
#                 timeout=15,
#             )
#             data = response.json()
#         except requests.RequestException as e:
#             logger.error('Bank verification failed: %s', e)
#             raise BankVerificationError('Could not reach bank verification service.')

#         if not data.get('status'):
#             raise BankVerificationError(
#                 data.get('message', 'Bank account verification failed.')
#             )

#         return data['data']['account_name']
"""
Payments service layer.

Thin orchestration between views (HTTP), provider classes (external APIs),
and the wallet (internal ledger). Two main responsibilities:

    1. PaymentService.initiate_deposit — picks the right provider and
       creates a Deposit row.
    2. PaymentService.process_deposit_completion — called from webhooks
       after signature verification. Credits the wallet atomically.

All wallet credits go through WalletService, which has its own idempotency
layer. This module's idempotency layer (Deposit.status check) is the first
line of defense; the wallet's reference_id check is the second.
"""
import logging
from decimal import Decimal
from typing import Optional

from django.db import transaction as db_transaction
from django.utils import timezone

from apps.wallet.models import Transaction
from apps.wallet.services import WalletService
from common.exceptions import ProviderError

from .models import Deposit, VirtualAccount
from .providers.base import PaymentProvider
from .providers.paystack import PaystackProvider
from .providers.monnify import MonnifyProvider
from .providers.nowpayments import NOWPaymentsProvider, get_ngn_per_usdt

logger = logging.getLogger(__name__)


def get_provider(name: str) -> PaymentProvider:
    """Provider registry. Add new providers here."""
    providers = {
        Deposit.Provider.PAYSTACK: PaystackProvider,
        Deposit.Provider.MONNIFY: MonnifyProvider,
        Deposit.Provider.NOWPAYMENTS: NOWPaymentsProvider,
    }
    cls = providers.get(name)
    if not cls:
        raise ProviderError(f'Unknown payment provider: {name}')
    return cls()


class PaymentService:

    # ─── Initiation ──────────────────────────────────────────────────────

    @staticmethod
    def initiate_deposit(user, amount: Decimal, provider: str) -> Deposit:
        """
        Create a deposit and return it. The provider populates payment_url
        (Paystack) or payment_address (Monnify, NOWPayments).

        NOT wrapped in a transaction — the provider makes a real external
        call we don't want rolled back if a downstream local DB error occurs.
        Each provider creates its Deposit row directly.
        """
        if amount <= 0:
            raise ValueError('Deposit amount must be positive.')

        provider_impl = get_provider(provider)
        return provider_impl.initiate_deposit(user, amount)

    # ─── Webhook completion ──────────────────────────────────────────────

    @staticmethod
    def process_deposit_completion(
        provider_name: str, parsed_webhook: dict,
    ) -> Optional[Deposit]:
        """
        Called from webhook views after signature verification.

        parsed_webhook = {
            'event_type': 'success' | 'failure',
            'reference': str,
            'amount': Decimal,
            'raw': dict,
            ...provider-specific extras...
        }

        Idempotent: replaying the same webhook is a safe no-op.
        Returns the Deposit row, or None if it couldn't be matched.
        """
        if provider_name == Deposit.Provider.MONNIFY:
            return PaymentService._process_monnify_completion(parsed_webhook)
        elif provider_name == Deposit.Provider.NOWPAYMENTS:
            return PaymentService._process_nowpayments_completion(parsed_webhook)
        else:
            return PaymentService._process_paystack_completion(parsed_webhook)

    # ─── Paystack completion ─────────────────────────────────────────────

    @staticmethod
    @db_transaction.atomic
    def _process_paystack_completion(parsed: dict) -> Optional[Deposit]:
        reference = parsed['reference']

        deposit = (
            Deposit.objects
            .select_for_update()
            .filter(internal_reference=reference,
                    provider=Deposit.Provider.PAYSTACK)
            .first()
        )
        if not deposit:
            logger.warning('Paystack webhook for unknown reference: %s', reference)
            return None

        return PaymentService._complete_or_fail(
            deposit=deposit,
            event_type=parsed['event_type'],
            confirmed_amount=parsed['amount'],  # Paystack reports in NGN
            credit_amount=deposit.amount,        # what we credit (NGN expected)
            raw=parsed['raw'],
        )

    # ─── Monnify completion ──────────────────────────────────────────────

    @staticmethod
    @db_transaction.atomic
    def _process_monnify_completion(parsed: dict) -> Optional[Deposit]:
        """
        Monnify webhooks are different: the user didn't initiate a deposit
        on our side. They just sent money to their virtual account. We
        create a Deposit row HERE on success, keyed by the Monnify
        transactionReference (parsed['reference']).
        """
        provider_tx_ref = parsed['reference']
        va_ref = parsed.get('virtual_account_reference', '')
        amount = parsed['amount']

        # Idempotency: have we processed this Monnify transaction before?
        existing = Deposit.objects.filter(
            provider=Deposit.Provider.MONNIFY,
            provider_reference=provider_tx_ref,
        ).first()
        if existing:
            logger.info('Idempotent Monnify replay: tx_ref=%s', provider_tx_ref)
            return existing

        # Find which user this VA belongs to
        va = VirtualAccount.objects.filter(
            monnify_account_reference=va_ref,
        ).first()
        if not va:
            logger.warning('Monnify webhook for unknown VA: %s', va_ref)
            return None

        # Create the Deposit row now (Monnify deposits are webhook-driven)
        import uuid
        deposit = Deposit.objects.create(
            user=va.user,
            amount=amount,
            provider=Deposit.Provider.MONNIFY,
            internal_reference=f'MON-WH-{uuid.uuid4().hex[:24].upper()}',
            provider_reference=provider_tx_ref,
            status=Deposit.Status.PENDING,
            provider_data={'webhook': parsed['raw']},
        )

        return PaymentService._complete_or_fail(
            deposit=deposit,
            event_type='success',
            confirmed_amount=amount,
            credit_amount=amount,
            raw=parsed['raw'],
        )

    # ─── NOWPayments completion ──────────────────────────────────────────

    @staticmethod
    @db_transaction.atomic
    def _process_nowpayments_completion(parsed: dict) -> Optional[Deposit]:
        reference = parsed['reference']  # our internal_reference (order_id)
        usdt_paid = parsed['amount']

        deposit = (
            Deposit.objects
            .select_for_update()
            .filter(internal_reference=reference,
                    provider=Deposit.Provider.NOWPAYMENTS)
            .first()
        )
        if not deposit:
            logger.warning('NOWPayments webhook for unknown reference: %s', reference)
            return None

        # Recompute NGN credit from actual USDT paid × locked rate.
        # If user underpaid, credit accordingly. If they overpaid, credit
        # the higher amount (we don't penalize for sending too much).
        rate = deposit.conversion_rate or get_ngn_per_usdt()
        ngn_credit = (usdt_paid * rate).quantize(Decimal('0.01'))

        return PaymentService._complete_or_fail(
            deposit=deposit,
            event_type=parsed['event_type'],
            confirmed_amount=usdt_paid,  # USDT
            credit_amount=ngn_credit,    # NGN
            raw=parsed['raw'],
        )

    # ─── Shared completion logic ─────────────────────────────────────────

    @staticmethod
    def _complete_or_fail(
        deposit: Deposit,
        event_type: str,
        confirmed_amount: Decimal,
        credit_amount: Decimal,
        raw: dict,
    ) -> Deposit:
        """
        Mark a deposit as completed (and credit wallet) or failed.
        Caller must hold a row-level lock on `deposit` already.
        """
        # Idempotency at the deposit level — already processed?
        if deposit.status == Deposit.Status.COMPLETED:
            logger.info('Deposit already completed: %s', deposit.id)
            return deposit
        if deposit.status == Deposit.Status.FAILED:
            logger.info('Deposit already marked failed: %s', deposit.id)
            return deposit

        if event_type != 'success':
            deposit.status = Deposit.Status.FAILED
            deposit.provider_data = {**deposit.provider_data, 'webhook': raw}
            deposit.webhook_received_at = timezone.now()
            deposit.save(update_fields=[
                'status', 'provider_data', 'webhook_received_at', 'updated_at',
            ])
            logger.info('Deposit failed: id=%s', deposit.id)
            return deposit

        # ── Success path: credit the wallet ──
        # Coins go to the COIN balance. NGN deposits credit Coins per PRD
        # (Coins are the play currency; Cash comes only from spin wins).
        wallet_tx = WalletService.credit(
            user=deposit.user,
            amount=credit_amount,
            balance_type='coin',
            tx_type=Transaction.Type.DEPOSIT,
            reference_id=f'deposit:{deposit.id}',
            metadata={
                'provider': deposit.provider,
                'deposit_id': str(deposit.id),
                'confirmed_amount': str(confirmed_amount),
                'original_currency': deposit.original_currency or 'NGN',
            },
        )

        deposit.status = Deposit.Status.COMPLETED
        deposit.wallet_transaction = wallet_tx
        deposit.provider_data = {**deposit.provider_data, 'webhook': raw}
        deposit.webhook_received_at = timezone.now()
        deposit.completed_at = timezone.now()
        deposit.save(update_fields=[
            'status', 'wallet_transaction', 'provider_data',
            'webhook_received_at', 'completed_at', 'updated_at',
        ])

        logger.info(
            'Deposit completed: id=%s user=%s amount=%s provider=%s',
            deposit.id, deposit.user.telegram_id, credit_amount, deposit.provider,
        )

        # Side effects (referrals, notifications) MUST be queued via on_commit
        # so they only fire after this transaction successfully commits.
        # Currently no-ops — uncomment when those modules exist.
        # from django.db.transaction import on_commit
        # on_commit(lambda: ReferralService.check_first_deposit(deposit.user.id))
        # on_commit(lambda: notify_deposit_success.delay(...))

        return deposit