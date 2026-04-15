import logging
from decimal import Decimal
from django.db import transaction as db_transaction
from django.db.models import Sum

from common.exceptions import InsufficientFundsError
from .models import Wallet, Transaction

logger = logging.getLogger(__name__)


class WalletService:

    @staticmethod
    def get_balance(user, balance_type: str = 'coin') -> Decimal:
        """Compute balance from ledger. Always accurate."""
        result = Transaction.objects.filter(
            user=user,
            balance_type=balance_type,
            status=Transaction.Status.COMPLETED,
        ).aggregate(total=Sum('amount'))
        return result['total'] or Decimal('0')

    @staticmethod
    @db_transaction.atomic
    def credit(
        user,
        amount: Decimal,
        balance_type: str,
        tx_type: str,
        reference_id: str,
        metadata: dict = None,
    ) -> Transaction:
        """
        Credit a user's wallet.
        Must be called inside an atomic block.
        """
        if amount <= 0:
            raise ValueError('Credit amount must be positive.')

        wallet = user.wallet

        # Lock wallet row to prevent race conditions
        Wallet.objects.select_for_update().get(pk=wallet.pk)

        balance_before = WalletService.get_balance(user, balance_type)
        balance_after = balance_before + amount

        tx = Transaction.objects.create(
            user=user,
            wallet=wallet,
            type=tx_type,
            balance_type=balance_type,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_id=reference_id,
            status=Transaction.Status.COMPLETED,
            metadata=metadata or {},
        )

        logger.info(
            'CREDIT user=%s type=%s amount=%s balance_type=%s ref=%s',
            user.telegram_id, tx_type, amount, balance_type, reference_id,
        )
        return tx

    @staticmethod
    @db_transaction.atomic
    def debit(
        user,
        amount: Decimal,
        balance_type: str,
        tx_type: str,
        reference_id: str,
        metadata: dict = None,
    ) -> Transaction:
        """
        Debit a user's wallet.
        Raises InsufficientFundsError if balance is too low.
        Must be called inside an atomic block.
        """
        if amount <= 0:
            raise ValueError('Debit amount must be positive.')

        wallet = user.wallet

        # Lock wallet row to prevent race conditions
        Wallet.objects.select_for_update().get(pk=wallet.pk)

        balance_before = WalletService.get_balance(user, balance_type)

        if balance_before < amount:
            raise InsufficientFundsError(
                f'Insufficient {balance_type} balance: {balance_before} < {amount}'
            )

        balance_after = balance_before - amount

        tx = Transaction.objects.create(
            user=user,
            wallet=wallet,
            type=tx_type,
            balance_type=balance_type,
            amount=-amount,  # stored as negative
            balance_before=balance_before,
            balance_after=balance_after,
            reference_id=reference_id,
            status=Transaction.Status.COMPLETED,
            metadata=metadata or {},
        )

        logger.info(
            'DEBIT user=%s type=%s amount=%s balance_type=%s ref=%s',
            user.telegram_id, tx_type, amount, balance_type, reference_id,
        )
        return tx

    @staticmethod
    def get_wallet_summary(user) -> dict:
        coin = WalletService.get_balance(user, 'coin')
        cash = WalletService.get_balance(user, 'cash')
        return {
            'coin_balance': str(coin),
            'cash_balance': str(cash),
            'total_balance': str(coin + cash),
        }
