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

from apps.settings_app.models import SettingKey
from apps.settings_app.services import get_setting
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService
from common.exceptions import ProviderError

from .models import Deposit, VirtualAccount
from .providers.base import PaymentProvider
from .providers.paystack import PaystackProvider
from .providers.monnify import MonnifyProvider
from .providers.nowpayments import NOWPaymentsProvider, get_ngn_per_usdt
from apps.notifications.services import NotificationService
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
        confirmed_amount: Decimal,    # The raw amount confirmed (NGN or USDT)
        credit_amount: Decimal,        # Ignored — we recompute coins here
        raw: dict,
    ) -> Deposit:
        """
        Mark a deposit as completed and credit DEPOSIT_COINS.
        The amount of coins credited depends on the provider:
        - Paystack: amount_ngn × COINS_PER_NGN
        - NowPayments: usdt_amount × COINS_PER_USD
        """
        # Idempotency
        if deposit.status == Deposit.Status.COMPLETED:
            logger.info('Deposit already completed: %s', deposit.id)
            return deposit
        if deposit.status == Deposit.Status.FAILED:
            return deposit

        if event_type != 'success':
            deposit.status = Deposit.Status.FAILED
            deposit.save()
            return deposit

        # ── Compute coins to credit based on provider ──
        if deposit.provider == Deposit.Provider.PAYSTACK:
            rate = get_setting(SettingKey.COINS_PER_NGN)
            coins = (confirmed_amount * rate).quantize(Decimal('0.01'))
            currency_label = 'NGN'
        elif deposit.provider == Deposit.Provider.NOWPAYMENTS:
            rate = get_setting(SettingKey.COINS_PER_USD)
            coins = (confirmed_amount * rate).quantize(Decimal('0.01'))
            currency_label = 'USD'
        else:
            coins = confirmed_amount
            currency_label = 'UNKNOWN'
            rate = None

        # ── Credit via WalletService (handles balance_before / balance_after / locking / idempotency) ──
        WalletService.credit(
            user=deposit.user,
            amount=coins,
            balance_type=Transaction.BalanceType.DEPOSIT_COINS,
            tx_type=Transaction.Type.DEPOSIT,
            reference_id=f'deposit-{deposit.id}',
            metadata={
                'deposit_id': str(deposit.id),
                'provider': deposit.provider,
                'confirmed_amount': str(confirmed_amount),
                'currency': currency_label,
                'rate': str(rate) if rate is not None else None,
                'coins_credited': str(coins),
            },
        )

        deposit.status = Deposit.Status.COMPLETED
        deposit.completed_at = timezone.now()
        deposit.save()

        logger.info(
            'Deposit completed: user=%s amount=%s coins=%s',
            deposit.user.id, confirmed_amount, coins,
        )
        NotificationService.send_async(
            telegram_id=deposit.user.telegram_id,
            notification_type='deposit_success',
            data={'amount': str(coins)},   # show coins credited
        )
        return deposit
    # def _complete_or_fail(
    #     deposit: Deposit,
    #     event_type: str,
    #     confirmed_amount: Decimal,
    #     credit_amount: Decimal,
    #     raw: dict,
    # ) -> Deposit:
    #     """
    #     Mark a deposit as completed (and credit wallet) or failed.
    #     Caller must hold a row-level lock on `deposit` already.
    #     """
    #     # Idempotency at the deposit level — already processed?
    #     if deposit.status == Deposit.Status.COMPLETED:
    #         logger.info('Deposit already completed: %s', deposit.id)
    #         return deposit
    #     if deposit.status == Deposit.Status.FAILED:
    #         logger.info('Deposit already marked failed: %s', deposit.id)
    #         return deposit

    #     if event_type != 'success':
    #         deposit.status = Deposit.Status.FAILED
    #         deposit.provider_data = {**deposit.provider_data, 'webhook': raw}
    #         deposit.webhook_received_at = timezone.now()
    #         deposit.save(update_fields=[
    #             'status', 'provider_data', 'webhook_received_at', 'updated_at',
    #         ])
    #         logger.info('Deposit failed: id=%s', deposit.id)
    #         return deposit

    #     # ── Success path: credit the wallet ──
    #     # Coins go to the COIN balance. NGN deposits credit Coins per PRD
    #     # (Coins are the play currency; Cash comes only from spin wins).
    #     wallet_tx = WalletService.credit(
    #         user=deposit.user,
    #         amount=credit_amount,
    #         balance_type='coin',
    #         tx_type=Transaction.Type.DEPOSIT,
    #         reference_id=f'deposit:{deposit.id}',
    #         metadata={
    #             'provider': deposit.provider,
    #             'deposit_id': str(deposit.id),
    #             'confirmed_amount': str(confirmed_amount),
    #             'original_currency': deposit.original_currency or 'NGN',
    #         },
    #     )

    #     deposit.status = Deposit.Status.COMPLETED
    #     deposit.wallet_transaction = wallet_tx
    #     deposit.provider_data = {**deposit.provider_data, 'webhook': raw}
    #     deposit.webhook_received_at = timezone.now()
    #     deposit.completed_at = timezone.now()
    #     deposit.save(update_fields=[
    #         'status', 'wallet_transaction', 'provider_data',
    #         'webhook_received_at', 'completed_at', 'updated_at',
    #     ])

    #     logger.info(
    #         'Deposit completed: id=%s user=%s amount=%s provider=%s',
    #         deposit.id, deposit.user.telegram_id, credit_amount, deposit.provider,
    #     )

    #     # Side effects (referrals, notifications) MUST be queued via on_commit
    #     # so they only fire after this transaction successfully commits.
    #     # Currently no-ops — uncomment when those modules exist.
    #     # from django.db.transaction import on_commit
    #     # on_commit(lambda: ReferralService.check_first_deposit(deposit.user.id))
    #     # on_commit(lambda: notify_deposit_success.delay(...))

    #     return deposit