"""
Wallet smoke test.

Exercises every WalletService method against your real backend. Proves:
  - Credit / debit work and update balances correctly
  - Idempotency: same reference_id never double-credits
  - Insufficient funds is rejected
  - Stake → Release (WIN) returns stake + winnings to cash
  - Stake → Forfeit (LOSS) removes stake permanently
  - The CHECK constraints in the DB actually catch violations
  - Sum of ledger == computed balance (the core invariant)

This script talks to the Django ORM directly inside the Docker container,
not via HTTP. That's deliberate — we're testing the service layer, not the
view layer. View tests come later.

Usage:
    docker compose exec api python /app/scripts/smoke_test_wallet.py

Or copy it to your project root and:
    docker compose exec api python smoke_test_wallet.py

What success looks like:
    All checks print '✓'. Final line: 'WALLET SMOKE TEST PASSED'.

What failure looks like:
    A check prints '✗' with details. Script exits non-zero. Read the message,
    fix the code, re-run. Failures here are bugs you want to catch BEFORE
    integrating the spin engine or payments module.
"""
import os
import sys
import django
from decimal import Decimal

# Bootstrap Django so we can use the ORM
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.db import IntegrityError, transaction as db_transaction
from django.db.models import Sum

from apps.users.models import User
from apps.wallet.models import Wallet, Transaction
from apps.wallet.services import WalletService
from common.exceptions import InsufficientFundsError, SpinRewardsException


# ─── Test infrastructure ─────────────────────────────────────────────────────

TESTS_RUN = 0
TESTS_PASSED = 0
TESTS_FAILED = 0


def check(condition: bool, label: str):
    global TESTS_RUN, TESTS_PASSED, TESTS_FAILED
    TESTS_RUN += 1
    if condition:
        TESTS_PASSED += 1
        print(f'  ✓ {label}')
    else:
        TESTS_FAILED += 1
        print(f'  ✗ {label}')


def section(title: str):
    print(f'\n{"=" * 60}')
    print(f'{title}')
    print(f'{"=" * 60}')


def cleanup_test_user(telegram_id: int):
    """Delete the test user and all associated data, if it exists."""
    try:
        u = User.objects.get(telegram_id=telegram_id)
        # Cascading deletes will handle wallet and transactions
        u.delete()
    except User.DoesNotExist:
        pass


def make_test_user(telegram_id: int = 800000001) -> User:
    """Create a fresh test user. Wallet is auto-created via post_save signal."""
    cleanup_test_user(telegram_id)
    user = User.objects.create_user(
        telegram_id=telegram_id,
        first_name='WalletSmoke',
        username='wallet_smoke_test',
    )
    # Sanity: wallet should already exist via the signal
    assert hasattr(user, 'wallet'), 'Wallet was not auto-created — signal not wired.'
    return user


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_initial_state(user):
    section('TEST 1: Initial state — all balances zero')
    check(WalletService.get_balance(user, 'coin') == Decimal('0'), 'coin starts at 0')
    check(WalletService.get_balance(user, 'cash') == Decimal('0'), 'cash starts at 0')
    check(WalletService.get_balance(user, 'staked') == Decimal('0'), 'staked starts at 0')

    summary = WalletService.get_wallet_summary(user)
    check(summary['coin_balance'] == '0', 'summary coin = 0')
    check(summary['cash_balance'] == '0', 'summary cash = 0')
    check(summary['staked_balance'] == '0', 'summary staked = 0')


def test_credit(user):
    section('TEST 2: Credit — adds money correctly')
    tx = WalletService.credit(
        user=user,
        amount=Decimal('1000'),
        balance_type='coin',
        tx_type=Transaction.Type.DEPOSIT,
        reference_id='smoke:deposit-001',
        metadata={'source': 'smoke_test'},
    )
    check(tx.amount == Decimal('1000'), 'tx.amount = 1000')
    check(tx.balance_before == Decimal('0'), 'balance_before = 0')
    check(tx.balance_after == Decimal('1000'), 'balance_after = 1000')
    check(tx.status == Transaction.Status.COMPLETED, 'status = completed')
    check(WalletService.get_balance(user, 'coin') == Decimal('1000'), 'balance is 1000')


def test_credit_idempotency(user):
    section('TEST 3: Credit idempotency — duplicate ref returns same tx')
    tx1 = WalletService.credit(
        user, Decimal('500'), 'coin',
        Transaction.Type.DEPOSIT, 'smoke:idempotency-001',
    )
    balance_after_first = WalletService.get_balance(user, 'coin')

    # Replay the EXACT same call
    tx2 = WalletService.credit(
        user, Decimal('500'), 'coin',
        Transaction.Type.DEPOSIT, 'smoke:idempotency-001',
    )
    balance_after_second = WalletService.get_balance(user, 'coin')

    check(tx1.id == tx2.id, 'returned the same Transaction')
    check(balance_after_first == balance_after_second, 'balance did not change on replay')


def test_credit_validation(user):
    section('TEST 4: Credit validation — rejects bad inputs')
    # Negative amount
    try:
        WalletService.credit(user, Decimal('-100'), 'coin',
                             Transaction.Type.DEPOSIT, 'smoke:bad-amount-001')
        check(False, 'rejected negative amount')
    except ValueError:
        check(True, 'rejected negative amount')

    # Zero amount
    try:
        WalletService.credit(user, Decimal('0'), 'coin',
                             Transaction.Type.DEPOSIT, 'smoke:bad-amount-002')
        check(False, 'rejected zero amount')
    except ValueError:
        check(True, 'rejected zero amount')

    # Bad balance type
    try:
        WalletService.credit(user, Decimal('100'), 'fakecurrency',
                             Transaction.Type.DEPOSIT, 'smoke:bad-type-001')
        check(False, 'rejected invalid balance_type')
    except ValueError:
        check(True, 'rejected invalid balance_type')


def test_debit(user):
    section('TEST 5: Debit — removes money correctly')
    initial = WalletService.get_balance(user, 'coin')
    tx = WalletService.debit(
        user, Decimal('300'), 'coin',
        Transaction.Type.WITHDRAWAL, 'smoke:debit-001',
    )
    check(tx.amount == Decimal('-300'), 'tx.amount stored as -300 (negative)')
    check(tx.balance_after == initial - Decimal('300'), 'balance_after correct')
    check(WalletService.get_balance(user, 'coin') == initial - Decimal('300'),
          'balance reduced by 300')


def test_debit_insufficient_funds(user):
    section('TEST 6: Debit — rejects when insufficient funds')
    huge = Decimal('999999999')
    try:
        WalletService.debit(user, huge, 'coin',
                            Transaction.Type.WITHDRAWAL, 'smoke:overdraft-001')
        check(False, 'rejected overdraft')
    except InsufficientFundsError:
        check(True, 'rejected overdraft with InsufficientFundsError')

    # Make sure no Transaction row was inserted
    found = Transaction.objects.filter(reference_id='smoke:overdraft-001').exists()
    check(not found, 'no Transaction row written on overdraft')


def test_stake_lock_and_release(user):
    section('TEST 7: Stake lock → release (WIN scenario)')
    # Top up coin so we can stake
    WalletService.credit(user, Decimal('5000'), 'coin',
                         Transaction.Type.DEPOSIT, 'smoke:stake-topup-001')

    coin_before = WalletService.get_balance(user, 'coin')
    cash_before = WalletService.get_balance(user, 'cash')
    staked_before = WalletService.get_balance(user, 'staked')

    # Place a 500 stake
    WalletService.lock(
        user, Decimal('500'), 'coin', 'smoke:spin-001',
        metadata={'wheel': 'standard'},
    )
    check(WalletService.get_balance(user, 'coin') == coin_before - Decimal('500'),
          'coin reduced by 500')
    check(WalletService.get_balance(user, 'staked') == staked_before + Decimal('500'),
          'staked increased by 500')

    # WIN: 3x multiplier means winnings = 1000 (user gets back 500 + 1000 = 1500)
    WalletService.release(
        user,
        lock_reference_id='smoke:spin-001',
        winnings=Decimal('1000'),
        resolve_reference_id='smoke:spin-001-result',
    )
    check(WalletService.get_balance(user, 'staked') == staked_before,
          'staked returned to original')
    check(WalletService.get_balance(user, 'cash') == cash_before + Decimal('1500'),
          'cash credited with stake + winnings (1500)')


def test_stake_lock_and_forfeit(user):
    section('TEST 8: Stake lock → forfeit (LOSS scenario)')
    cash_before = WalletService.get_balance(user, 'cash')
    coin_before = WalletService.get_balance(user, 'coin')
    staked_before = WalletService.get_balance(user, 'staked')

    WalletService.lock(
        user, Decimal('500'), 'coin', 'smoke:spin-002',
    )
    WalletService.forfeit(
        user,
        lock_reference_id='smoke:spin-002',
        resolve_reference_id='smoke:spin-002-result',
    )

    check(WalletService.get_balance(user, 'staked') == staked_before,
          'staked back to original (zero held)')
    check(WalletService.get_balance(user, 'cash') == cash_before,
          'cash unchanged (loss does not credit cash)')
    check(WalletService.get_balance(user, 'coin') == coin_before - Decimal('500'),
          'coin reduced by 500 (the lost stake)')


def test_stake_idempotency(user):
    section('TEST 9: Stake operations are idempotent')
    coin_before = WalletService.get_balance(user, 'coin')

    # First lock
    tx1 = WalletService.lock(user, Decimal('200'), 'coin', 'smoke:spin-003')
    coin_after_lock = WalletService.get_balance(user, 'coin')

    # Replay the same lock — should be a no-op
    tx2 = WalletService.lock(user, Decimal('200'), 'coin', 'smoke:spin-003')
    coin_after_replay = WalletService.get_balance(user, 'coin')

    check(tx1.id == tx2.id, 'lock returned same tx on replay')
    check(coin_after_lock == coin_after_replay, 'balance unchanged on replay')

    # Cleanup: forfeit so we don't leave funds locked
    WalletService.forfeit(user, 'smoke:spin-003', 'smoke:spin-003-cleanup')


def test_stake_insufficient_funds(user):
    section('TEST 10: Stake — rejects when insufficient source balance')
    huge = Decimal('999999999')
    try:
        WalletService.lock(user, huge, 'coin', 'smoke:spin-overdraft-001')
        check(False, 'rejected overdraft on lock')
    except InsufficientFundsError:
        check(True, 'rejected overdraft on lock')


def test_release_without_lock(user):
    section('TEST 11: Release without prior lock — rejected')
    try:
        WalletService.release(
            user,
            lock_reference_id='smoke:nonexistent-lock',
            winnings=Decimal('1000'),
            resolve_reference_id='smoke:phantom-resolve-001',
        )
        check(False, 'rejected release without lock')
    except SpinRewardsException:
        check(True, 'rejected release without lock')


def test_db_check_constraint():
    """
    Verify Postgres CHECK constraints are actually in place.
    We bypass the service layer and try to insert garbage directly.
    """
    section('TEST 12: DB CHECK constraints catch invalid inserts')

    # Try to insert a tx where balance_after != balance_before + amount
    # (should be caught by wallet_balance_arithmetic_consistent)
    user = User.objects.filter(telegram_id=800000001).first()
    if not user:
        check(False, 'test user not found')
        return

    try:
        with db_transaction.atomic():
            Transaction.objects.create(
                user=user,
                wallet=user.wallet,
                type=Transaction.Type.DEPOSIT,
                balance_type='coin',
                amount=Decimal('100'),
                balance_before=Decimal('0'),
                balance_after=Decimal('999'),  # WRONG — should be 100
                reference_id='smoke:bad-arithmetic-001',
            )
        check(False, 'arithmetic CHECK constraint did NOT fire')
    except IntegrityError:
        check(True, 'arithmetic CHECK constraint caught inconsistent balance_after')

    # Try to insert a tx that ends with negative balance_after
    # (should be caught by wallet_balance_never_negative)
    try:
        with db_transaction.atomic():
            Transaction.objects.create(
                user=user,
                wallet=user.wallet,
                type=Transaction.Type.WITHDRAWAL,
                balance_type='coin',
                amount=Decimal('-99999999'),
                balance_before=Decimal('100'),
                balance_after=Decimal('-99999899'),  # negative — banned
                reference_id='smoke:bad-negative-001',
            )
        check(False, 'negative-balance CHECK constraint did NOT fire')
    except IntegrityError:
        check(True, 'negative-balance CHECK constraint caught negative balance_after')


def test_ledger_invariant(user):
    """
    The fundamental invariant of a ledger system:
        sum(all completed transactions for user, balance_type) == get_balance()

    If this ever fails, you have a bug.
    """
    section('TEST 13: Ledger invariant — sum of txs == computed balance')

    for balance_type in ('coin', 'cash', 'staked'):
        ledger_sum = Transaction.objects.filter(
            user=user,
            balance_type=balance_type,
            status=Transaction.Status.COMPLETED,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        service_balance = WalletService.get_balance(user, balance_type)

        check(ledger_sum == service_balance,
              f'{balance_type}: ledger_sum ({ledger_sum}) == service ({service_balance})')


def test_balance_arithmetic_chain(user):
    """
    Walk through the user's transactions in order and verify each one's
    balance_before / balance_after correctly chains from the previous tx
    of the same balance_type.
    """
    section('TEST 14: Transaction chain — balance_before/after consistency')

    for balance_type in ('coin', 'cash', 'staked'):
        txs = list(Transaction.objects.filter(
            user=user,
            balance_type=balance_type,
            status=Transaction.Status.COMPLETED,
        ).order_by('created_at'))

        running_balance = Decimal('0')
        all_ok = True
        for tx in txs:
            if tx.balance_before != running_balance:
                all_ok = False
                print(f'    BREAK: {balance_type} tx {tx.id} '
                      f'has balance_before={tx.balance_before}, expected={running_balance}')
                break
            running_balance += tx.amount
            if tx.balance_after != running_balance:
                all_ok = False
                print(f'    BREAK: {balance_type} tx {tx.id} '
                      f'has balance_after={tx.balance_after}, expected={running_balance}')
                break

        check(all_ok, f'{balance_type}: {len(txs)} transactions chain correctly')


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print('SPIN REWARDS — WALLET SMOKE TEST')
    print('Testing WalletService against the live Postgres database.\n')

    user = make_test_user()
    print(f'→ Test user: telegram_id={user.telegram_id}')
    print(f'→ Wallet ID: {user.wallet.id}')

    try:
        test_initial_state(user)
        test_credit(user)
        test_credit_idempotency(user)
        test_credit_validation(user)
        test_debit(user)
        test_debit_insufficient_funds(user)
        test_stake_lock_and_release(user)
        test_stake_lock_and_forfeit(user)
        test_stake_idempotency(user)
        test_stake_insufficient_funds(user)
        test_release_without_lock(user)
        test_db_check_constraint()
        test_ledger_invariant(user)
        test_balance_arithmetic_chain(user)
    finally:
        # Clean up test data so re-runs start fresh
        cleanup_test_user(800000001)

    print(f'\n{"=" * 60}')
    print(f'RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print(f'{"=" * 60}')

    if TESTS_FAILED:
        print('\n❌ WALLET SMOKE TEST FAILED')
        sys.exit(1)
    else:
        print('\n✅ WALLET SMOKE TEST PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()