"""
Wallet services — the only sanctioned way to mutate a user's wallet.

Every operation is wrapped in @transaction.atomic and acquires a row-level
lock on the user's Wallet row before reading the ledger. This serializes all
mutations for a given user and prevents the classic credit/debit race.

Public API:
  WalletService.get_balance(user, balance_type)           — read
  WalletService.get_wallet_summary(user)                  — read all balances
  WalletService.credit(user, amount, ...)                 — add money
  WalletService.debit(user, amount, ...)                  — remove money
  WalletService.lock(user, amount, source_balance_type, ...)   — stake
  WalletService.release(user, lock_reference_id, return_amount, ...)  — win
  WalletService.forfeit(user, lock_reference_id, ...)     — loss

Idempotency:
  Every method takes a reference_id. If the same reference_id is replayed,
  the existing Transaction is returned and no balance change occurs. This
  makes wallet mutations safe for webhook retries.
"""
import logging
from decimal import Decimal
from typing import Optional

from django.db import transaction as db_transaction

from common.exceptions import (
    InsufficientFundsError,
    SpinRewardsException,
    DuplicateRequestError,
)
from .models import Wallet, Transaction


logger = logging.getLogger(__name__)


class WalletService:

    # ─── Reads ───────────────────────────────────────────────────────────
    BALANCE_TYPE_CURRENCY = {
        Transaction.BalanceType.CRYPTO_COINS: Transaction.Currency.USDT,
        Transaction.BalanceType.NAIRA_COINS: Transaction.Currency.NGN,
        Transaction.BalanceType.BONUS_COINS: Transaction.Currency.NGN,  # bonus coins are platform-issued; default to NGN
        Transaction.BalanceType.CRYPTO_WITHDRAW: Transaction.Currency.USDT,
        Transaction.BalanceType.NAIRA_WITHDRAW: Transaction.Currency.NGN,
        Transaction.BalanceType.STAKED: Transaction.Currency.NGN,  # transient — currency follows source; override at call site if needed
    }

    # @staticmethod
    # def get_balance(user, balance_type: str = 'coin') -> Decimal:
    #     """
    #     Read current balance from the ledger. Always accurate.

    #     For most callers this is fine. Inside a mutation, prefer the locked
    #     helper below to avoid TOCTOU (time-of-check vs time-of-use) bugs.
    #     """
    #     return Transaction.objects.balance_for(user, balance_type)
    # @staticmethod
    # def get_balance(user, balance_type: str) -> Decimal:
    # # def _validate_balance_type(balance_type: str) -> None:
    #     """Raise ValueError if balance_type isn't in the BalanceType enum."""
    #     valid = {c[0] for c in Transaction.BalanceType.choices}
    #     if balance_type not in valid:
    #         raise ValueError(
    #             f'Invalid balance_type: {balance_type!r}. Must be one of {sorted(valid)}.'
    #         )
    @staticmethod
    def get_balance(user, balance_type: str) -> Decimal:
        """
        Read current balance from the ledger. Always accurate.

        For most callers this is fine. Inside a mutation, prefer the locked
        helper below to avoid TOCTOU (time-of-check vs time-of-use) bugs.
        """
        WalletService._validate_balance_type(balance_type)
        balance = Transaction.objects.balance_for(user, balance_type)
        return balance if balance is not None else Decimal('0')

    @staticmethod
    def _validate_balance_type(balance_type: str) -> None:
        """Raise ValueError if balance_type isn't in the BalanceType enum."""
        valid = {c[0] for c in Transaction.BalanceType.choices}
        if balance_type not in valid:
            raise ValueError(
                f'Invalid balance_type: {balance_type!r}. Must be one of {sorted(valid)}.'
            )

    @staticmethod
    def _get_existing_tx(reference_id: str) -> Optional[Transaction]:
        """Idempotency check. Returns the existing tx if reference was used."""
        return Transaction.objects.filter(reference_id=reference_id).first()

    @staticmethod
    def _lock_wallet(user) -> Wallet:
        """
        Acquire the per-user wallet lock.

        MUST be called inside an atomic block. The lock is held until the
        atomic block commits, serializing all wallet mutations for this user.

        We lock the Wallet row (not the Transaction table) because:
          - Wallet has exactly one row per user (stable lock target)
          - Transaction is append-only and has many rows (no single row to lock)
          - The Wallet row's only purpose IS to serve as this lock
        """
        return Wallet.objects.select_for_update().get(user=user)
    @staticmethod
    def _resolve_currency(balance_type: str) -> str:
        """Return the canonical currency for a balance_type."""
        return WalletService.BALANCE_TYPE_CURRENCY.get(
            balance_type, Transaction.Currency.NGN
        )

    @staticmethod
    def _record_tx(
        wallet: Wallet,
        user,
        tx_type: str,
        balance_type: str,
        signed_amount: Decimal,
        reference_id: str,
        metadata: Optional[dict] = None,
        status: str = Transaction.Status.COMPLETED,
    ) -> Transaction:
        """
        Append a transaction to the ledger.

        Caller must already hold the wallet lock and have validated funds.
        balance_before is computed inside this method while the lock is held,
        so it reflects the true state at insertion time.
        """
        balance_before = Transaction.objects.balance_for(user, balance_type)
        balance_after = balance_before + signed_amount

        return Transaction.objects.create(
            user=user,
            wallet=wallet,
            type=tx_type,
            balance_type=balance_type,
            currency=WalletService._resolve_currency(balance_type),
            amount=signed_amount,
            balance_before=balance_before,
            balance_after=balance_after,
            reference_id=reference_id,
            status=status,
            metadata=metadata or {},
        )

    # ─── Mutations ───────────────────────────────────────────────────────

    @staticmethod
    @db_transaction.atomic
    def credit(
        user,
        amount: Decimal,
        balance_type: str,
        tx_type: str,
        reference_id: str,
        metadata: Optional[dict] = None,
    ) -> Transaction:
        """
        Add `amount` to the user's `balance_type` balance.

        Idempotent: if `reference_id` was used before, returns the existing
        Transaction without making any change.
        """
        if amount <= 0:
            raise ValueError('Credit amount must be positive.')
        
        WalletService._validate_balance_type(balance_type)


        existing = WalletService._get_existing_tx(reference_id)
        if existing:
            logger.info('Idempotent credit replay: ref=%s', reference_id)
            return existing

        wallet = WalletService._lock_wallet(user)

        # Re-check idempotency *after* acquiring the lock to close the
        # check-then-act race window with concurrent retries.
        existing = WalletService._get_existing_tx(reference_id)
        if existing:
            return existing

        tx = WalletService._record_tx(
            wallet=wallet,
            user=user,
            tx_type=tx_type,
            balance_type=balance_type,
            signed_amount=amount,
            reference_id=reference_id,
            metadata=metadata,
        )

        logger.info(
            'CREDIT user=%s type=%s amount=%s balance=%s ref=%s',
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
        metadata: Optional[dict] = None,
    ) -> Transaction:
        """
        Remove `amount` from the user's `balance_type` balance.

        Raises InsufficientFundsError if the balance is too low.
        Idempotent on reference_id.
        """
        if amount <= 0:
            raise ValueError('Debit amount must be positive.')
        WalletService._validate_balance_type(balance_type)

        existing = WalletService._get_existing_tx(reference_id)
        if existing:
            logger.info('Idempotent debit replay: ref=%s', reference_id)
            return existing

        wallet = WalletService._lock_wallet(user)

        existing = WalletService._get_existing_tx(reference_id)
        if existing:
            return existing

        balance_before = Transaction.objects.balance_for(user, balance_type)
        if balance_before < amount:
            raise InsufficientFundsError(
                f'Insufficient {balance_type} balance: {balance_before} < {amount}'
            )

        tx = WalletService._record_tx(
            wallet=wallet,
            user=user,
            tx_type=tx_type,
            balance_type=balance_type,
            signed_amount=-amount,
            reference_id=reference_id,
            metadata=metadata,
        )

        logger.info(
            'DEBIT user=%s type=%s amount=%s balance=%s ref=%s',
            user.telegram_id, tx_type, amount, balance_type, reference_id,
        )
        return tx

    

    @staticmethod
    @db_transaction.atomic
    def lock(
        user,
        amount: Decimal,
        source_balance_type: str,
        reference_id: str,
        metadata: Optional[dict] = None,
    ) -> Transaction:
        """
        Move `amount` from `source_balance_type` (coin or cash) into staked.

        Returns the staked-side transaction (the one with positive amount).
        Raises InsufficientFundsError if the source balance is too low.
        Idempotent on reference_id.
        """
        if amount <= 0:
            raise ValueError('Lock amount must be positive.')
        # valid_lock_sources = (
        #     Transaction.BalanceType.DEPOSIT_COINS,
        #     Transaction.BalanceType.BONUS_COINS,
        # )
        # if source_balance_type not in valid_lock_sources:
        #     raise ValueError(
        #         f'Cannot lock from {source_balance_type}; '
        #         f'use deposit_coins or bonus_coins.'
        #     )
        valid_lock_sources = (
            Transaction.BalanceType.CRYPTO_COINS,
            Transaction.BalanceType.NAIRA_COINS,
            Transaction.BalanceType.BONUS_COINS,
        )
        if source_balance_type not in valid_lock_sources:
            raise ValueError(
                f'Cannot lock from {source_balance_type}; '
                f'use crypto_coins, naira_coins, or bonus_coins.'
            )

        # Look for the staked-side leg (the canonical one)
        staked_ref = f'{reference_id}:lock'
        existing = WalletService._get_existing_tx(staked_ref)
        if existing:
            logger.info('Idempotent lock replay: ref=%s', reference_id)
            return existing

        wallet = WalletService._lock_wallet(user)

        existing = WalletService._get_existing_tx(staked_ref)
        if existing:
            return existing

        source_balance = Transaction.objects.balance_for(user, source_balance_type)
        if source_balance < amount:
            raise InsufficientFundsError(
                f'Insufficient {source_balance_type} for stake: '
                f'{source_balance} < {amount}'
            )

        # Leg 1: debit the source balance
        WalletService._record_tx(
            wallet=wallet,
            user=user,
            tx_type=Transaction.Type.STAKE,
            balance_type=source_balance_type,
            signed_amount=-amount,
            reference_id=f'{reference_id}:debit',
            metadata={'lock_ref': reference_id, **(metadata or {})},
        )

        # Leg 2: credit the staked balance
        staked_tx = WalletService._record_tx(
            wallet=wallet,
            user=user,
            tx_type=Transaction.Type.STAKE,
            balance_type='staked',
            signed_amount=amount,
            reference_id=staked_ref,
            metadata={
                'source_balance': source_balance_type,
                'lock_ref': reference_id,
                **(metadata or {}),
            },
        )

        logger.info(
            'LOCK user=%s amount=%s source=%s ref=%s',
            user.telegram_id, amount, source_balance_type, reference_id,
        )
        return staked_tx

    @staticmethod
    @db_transaction.atomic
    def release(
        user,
        lock_reference_id: str,
        winnings: Decimal,
        resolve_reference_id: str,
        metadata: Optional[dict] = None,
    ) -> Transaction:
        """
        WIN resolution: unlock the staked amount and credit (stake + winnings)
        to the user's cash balance.

        `winnings` is the amount ON TOP of the original stake. So a 3x multi
        on a ₦500 stake means winnings=1000 (user gets back 500 + 1000 = 1500).

        Idempotent on resolve_reference_id.
        """
        if winnings < 0:
            raise ValueError('Winnings cannot be negative; use forfeit() for losses.')

        existing = WalletService._get_existing_tx(f'{resolve_reference_id}:cash')
        if existing:
            logger.info('Idempotent release replay: ref=%s', resolve_reference_id)
            return existing

        wallet = WalletService._lock_wallet(user)

        existing = WalletService._get_existing_tx(f'{resolve_reference_id}:cash')
        if existing:
            return existing

        # Find the original lock to determine stake amount
        lock_tx = Transaction.objects.filter(
            reference_id=f'{lock_reference_id}:lock',
            user=user,
            balance_type='staked',
        ).first()
        if not lock_tx:
            raise SpinRewardsException(
                f'No matching lock found for {lock_reference_id}'
            )

        stake_amount = lock_tx.amount  # positive
        total_payout = stake_amount + winnings

        # Leg 1: debit staked (the stake comes out of lock)
        WalletService._record_tx(
            wallet=wallet,
            user=user,
            tx_type=Transaction.Type.STAKE_RELEASE,
            balance_type='staked',
            signed_amount=-stake_amount,
            reference_id=f'{resolve_reference_id}:unlock',
            metadata={'lock_ref': lock_reference_id, **(metadata or {})},
        )

        # Leg 2: credit cash with the full payout (stake + winnings)
        win_tx = WalletService._record_tx(
            wallet=wallet,
            user=user,
            tx_type=Transaction.Type.WIN,
            balance_type=Transaction.BalanceType.NAIRA_WITHDRAW,
            signed_amount=total_payout,
            reference_id=f'{resolve_reference_id}:cash',
            metadata={
                'lock_ref': lock_reference_id,
                'stake': str(stake_amount),
                'winnings': str(winnings),
                **(metadata or {}),
            },
        )

        logger.info(
            'RELEASE user=%s stake=%s winnings=%s payout=%s lock_ref=%s',
            user.telegram_id, stake_amount, winnings, total_payout,
            lock_reference_id,
        )
        return win_tx

    @staticmethod
    @db_transaction.atomic
    def forfeit(
        user,
        lock_reference_id: str,
        resolve_reference_id: str,
        metadata: Optional[dict] = None,
    ) -> Transaction:
        """
        LOSS resolution: remove the staked amount from the user's wallet
        permanently. The house keeps it.

        Idempotent on resolve_reference_id.
        """
        existing = WalletService._get_existing_tx(f'{resolve_reference_id}:forfeit')
        if existing:
            logger.info('Idempotent forfeit replay: ref=%s', resolve_reference_id)
            return existing

        wallet = WalletService._lock_wallet(user)

        existing = WalletService._get_existing_tx(f'{resolve_reference_id}:forfeit')
        if existing:
            return existing

        lock_tx = Transaction.objects.filter(
            reference_id=f'{lock_reference_id}:lock',
            user=user,
            balance_type='staked',
        ).first()
        if not lock_tx:
            raise SpinRewardsException(
                f'No matching lock found for {lock_reference_id}'
            )

        stake_amount = lock_tx.amount  # positive

        forfeit_tx = WalletService._record_tx(
            wallet=wallet,
            user=user,
            tx_type=Transaction.Type.STAKE_FORFEIT,
            balance_type='staked',
            signed_amount=-stake_amount,
            reference_id=f'{resolve_reference_id}:forfeit',
            metadata={'lock_ref': lock_reference_id, **(metadata or {})},
        )

        logger.info(
            'FORFEIT user=%s stake=%s lock_ref=%s',
            user.telegram_id, stake_amount, lock_reference_id,
        )
        return forfeit_tx
    
    # @staticmethod
    # def get_deposit_coins(user) -> Decimal:
    #     """Coins from real deposits (Paystack/NowPayments)."""
    #     return WalletService.get_balance(user, Transaction.BalanceType.DEPOSIT_COINS)
    
    
    # @staticmethod
    # def get_bonus_coins(user) -> Decimal:
    #     """Coins from challenge bonus_credit rewards."""
    #     return WalletService.get_balance(user, Transaction.BalanceType.BONUS_COINS)
    
    
    # @staticmethod
    # def get_earnings(user) -> Decimal:
    #     """Withdrawable naira balance."""
    #     return WalletService.get_balance(user, Transaction.BalanceType.EARNINGS)
    
    
    # @staticmethod
    # def get_total_coins(user) -> Decimal:
    #     """Sum of deposit + bonus coins. For the wallet card's headline number."""
    #     return (
    #         WalletService.get_deposit_coins(user)
    #         + WalletService.get_bonus_coins(user)
    #     )
    @staticmethod
    def get_crypto_coins(user) -> Decimal:
    # """Spendable USDT-denominated coins."""
        return WalletService.get_balance(user, Transaction.BalanceType.CRYPTO_COINS)
 
    @staticmethod
    def get_naira_coins(user) -> Decimal:
        """Spendable NGN-denominated coins."""
        return WalletService.get_balance(user, Transaction.BalanceType.NAIRA_COINS)
    
    @staticmethod
    def get_bonus_coins(user) -> Decimal:
        """Spendable bonus coins (platform-pegged)."""
        return WalletService.get_balance(user, Transaction.BalanceType.BONUS_COINS)
    
    @staticmethod
    def get_crypto_withdraw_balance(user) -> Decimal:
        """USDT withdrawable balance."""
        return WalletService.get_balance(user, Transaction.BalanceType.CRYPTO_WITHDRAW)
    
    @staticmethod
    def get_naira_withdraw_balance(user) -> Decimal:
        """NGN withdrawable balance."""
        return WalletService.get_balance(user, Transaction.BalanceType.NAIRA_WITHDRAW)
    
    
    # @staticmethod
    # def get_wallet_summary(user) -> dict:
    #     """
    #     Full wallet snapshot for the API.
    
    #     Returns:
    #         {
    #             'deposit_coins': '50000.00',
    #             'bonus_coins': '14800.00',
    #             'total_coins': '64800.00',
    #             'earnings': '600.00',
    #             'earnings_usd_equivalent': '0.40',
    #             'staked': '0.00',
    #         }
    #     """
    #     from apps.settings_app.services import get_setting
    #     from apps.settings_app.models import SettingKey
    
    #     deposit = WalletService.get_deposit_coins(user)
    #     bonus = WalletService.get_bonus_coins(user)
    #     earnings = WalletService.get_earnings(user)
    #     staked = WalletService.get_balance(user, Transaction.BalanceType.STAKED)
    
    #     # USD equivalent for earnings (display only)
    #     ngn_per_usd = get_setting(SettingKey.NGN_PER_USD_DISPLAY_RATE)
    #     usd_equivalent = (
    #         (earnings / ngn_per_usd).quantize(Decimal('0.01'))
    #         if ngn_per_usd > 0 else Decimal('0.00')
    #     )
    
    #     return {
    #         'deposit_coins': str(deposit),
    #         'bonus_coins': str(bonus),
    #         'total_coins': str(deposit + bonus),
    #         'earnings': str(earnings),
    #         'earnings_usd_equivalent': str(usd_equivalent),
    #         'staked': str(staked),
    #     }
    @staticmethod
    def get_wallet_summary(user) -> dict:
        """
        v3 wallet snapshot — 3 spendable coin buckets + 2 withdrawable balances.
    
        Returns:
            {
                'crypto_coins': '0.000000',
                'naira_coins': '50000.00',
                'bonus_coins': '1200.00',
                'crypto_withdraw_balance': '0.000000',
                'naira_withdraw_balance': '6000.00',
                'naira_withdraw_usd_equivalent': '4.00',
                'staked': '0.00'
            }
        """
        from apps.settings_app.services import get_setting
        from apps.settings_app.models import SettingKey
    
        crypto_coins = WalletService.get_balance(user, Transaction.BalanceType.CRYPTO_COINS)
        naira_coins = WalletService.get_balance(user, Transaction.BalanceType.NAIRA_COINS)
        bonus_coins = WalletService.get_balance(user, Transaction.BalanceType.BONUS_COINS)
        crypto_withdraw = WalletService.get_balance(user, Transaction.BalanceType.CRYPTO_WITHDRAW)
        naira_withdraw = WalletService.get_balance(user, Transaction.BalanceType.NAIRA_WITHDRAW)
        staked = WalletService.get_balance(user, Transaction.BalanceType.STAKED)
    
        # Display-only USD equivalent of the naira withdraw balance
        ngn_per_usd = get_setting(SettingKey.NGN_PER_USD_DISPLAY_RATE)
        usd_equivalent = (
            (naira_withdraw / ngn_per_usd).quantize(Decimal('0.01'))
            if ngn_per_usd > 0 else Decimal('0.00')
        )
    
        return {
            'crypto_coins': str(crypto_coins.quantize(Decimal('0.000001'))),
            'naira_coins': str(naira_coins.quantize(Decimal('0.01'))),
            'bonus_coins': str(bonus_coins.quantize(Decimal('0.01'))),
            'crypto_withdraw_balance': str(crypto_withdraw.quantize(Decimal('0.000001'))),
            'naira_withdraw_balance': str(naira_withdraw.quantize(Decimal('0.01'))),
            'naira_withdraw_usd_equivalent': str(usd_equivalent),
            'staked': str(staked.quantize(Decimal('0.01'))),
        }
