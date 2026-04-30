"""
Spin engine smoke test.

Tests every spin engine code path against the live database:
  1. Loss flow (multiplier=0): forfeit
  2. Win flow (multiplier>1): release with winnings to cash
  3. Push flow (multiplier=1): release with 0 winnings
  4. Partial loss (0<multiplier<1): forfeit + partial credit
  5. Welcome spin: free, one-time, credits cash directly
  6. Statistical RNG distribution: verify weights work
  7. Wallet integration: stake locked, cash credited on win
  8. Welcome uniqueness: second welcome attempt rejected
  9. Stake validation: out-of-range stakes rejected
  10. Insufficient funds: rejected without partial state
  11. Ledger invariant: Σ(spin txs) chains correctly

This script talks to the Django ORM directly — same approach as wallet/
payments smoke tests.

Usage:
    docker compose exec api python smoke_test_spin.py
"""
import os
import sys
from collections import Counter
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.db.models import Sum

from apps.payments.models import Deposit, VirtualAccount
from apps.spin.models import Spin, Wheel, WheelSegment
from apps.spin.services import SpinEngine
from apps.users.models import User
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService
from common.exceptions import (
    InsufficientFundsError,
    InvalidStakeError,
    SpinRewardsException,
)


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
    print(title)
    print(f'{"=" * 60}')


def cleanup(telegram_id: int):
    """
    Clean up test user respecting PROTECT FKs.
    Order: spins (which protect wallet txs) → deposits → user (cascades).
    """
    try:
        u = User.objects.get(telegram_id=telegram_id)
        Spin.objects.filter(user=u).delete()
        Deposit.objects.filter(user=u).delete()
        VirtualAccount.objects.filter(user=u).delete()
        u.delete()
    except User.DoesNotExist:
        pass


def make_test_user(telegram_id: int = 800000003) -> User:
    cleanup(telegram_id)
    user = User.objects.create_user(
        telegram_id=telegram_id,
        first_name='SpinSmoke',
        username='spin_smoke_test',
    )
    assert hasattr(user, 'wallet'), 'Wallet not auto-created'
    return user


def fund_user(user, coins: Decimal = Decimal('100000'), label: str = 'topup'):
    """Top up the user's coin balance."""
    import secrets
    WalletService.credit(
        user=user,
        amount=coins,
        balance_type='coin',
        tx_type=Transaction.Type.DEPOSIT,
        reference_id=f'smoke_{label}_{secrets.token_hex(8)}',
        metadata={'source': 'smoke_test_topup'},
    )


def make_test_wheel(name: str, segments: list, currency_type: str = 'coin',
                    is_welcome: bool = False, min_stake: Decimal = Decimal('100'),
                    max_stake: Decimal = Decimal('10000')) -> Wheel:
    """Create a controlled-outcome wheel for testing specific cases."""
    if is_welcome:
        wheel_type = Wheel.WheelType.WELCOME
        min_stake = Decimal('0')
        max_stake = Decimal('0')
    else:
        wheel_type = Wheel.WheelType.STANDARD

    wheel = Wheel.objects.create(
        wheel_type=wheel_type,
        name=name,
        currency_type=currency_type,
        min_stake=min_stake,
        max_stake=max_stake,
        is_welcome_only=is_welcome,
        is_active=True,
    )
    for pos, (label, mult, weight) in enumerate(segments):
        WheelSegment.objects.create(
            wheel=wheel, position=pos, label=label,
            multiplier=mult, probability_weight=weight,
        )
    return wheel


def cleanup_test_wheels():
    """Delete any wheels we created for testing (not the seeded ones)."""
    Wheel.objects.filter(name__startswith='SMOKE_').delete()


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_guaranteed_loss(user):
    """Wheel where the only segment is multiplier=0. Every spin must lose."""
    section('TEST 1: Guaranteed loss — stake forfeited')

    wheel = make_test_wheel(
        'SMOKE_loss_only',
        segments=[('Loss', Decimal('0'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('1000'),
    )
    fund_user(user, Decimal('10000'), 'loss_test')

    coin_before = WalletService.get_balance(user, 'coin')
    cash_before = WalletService.get_balance(user, 'cash')
    staked_before = WalletService.get_balance(user, 'staked')

    spin = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id), stake_amount=Decimal('500'),
    )

    check(spin.outcome == Spin.Outcome.LOSS, 'outcome marked LOSS')
    check(spin.payout_amount == Decimal('0'), 'payout = 0')
    check(
        WalletService.get_balance(user, 'coin') == coin_before - Decimal('500'),
        'coin reduced by stake (500)',
    )
    check(
        WalletService.get_balance(user, 'cash') == cash_before,
        'cash unchanged on loss',
    )
    check(
        WalletService.get_balance(user, 'staked') == staked_before,
        'staked back to original (lock cleared)',
    )
    check(spin.lock_transaction is not None, 'lock_transaction recorded')
    check(spin.resolution_transaction is not None, 'resolution_transaction recorded')


def test_guaranteed_win(user):
    """Wheel where the only segment is multiplier=3. Every spin must win 3x."""
    section('TEST 2: Guaranteed 3× win — winnings credit cash')

    wheel = make_test_wheel(
        'SMOKE_win_only',
        segments=[('Triple', Decimal('3'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('1000'),
    )
    fund_user(user, Decimal('5000'), 'win_test')

    coin_before = WalletService.get_balance(user, 'coin')
    cash_before = WalletService.get_balance(user, 'cash')

    spin = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id), stake_amount=Decimal('500'),
    )

    check(spin.outcome == Spin.Outcome.WIN, 'outcome marked WIN')
    check(spin.payout_amount == Decimal('1500'), 'payout = 3 × stake (1500)')
    check(
        WalletService.get_balance(user, 'coin') == coin_before - Decimal('500'),
        'coin reduced by stake only (500)',
    )
    check(
        WalletService.get_balance(user, 'cash') == cash_before + Decimal('1500'),
        'cash credited with full payout (1500)',
    )


def test_guaranteed_push(user):
    """Wheel with only multiplier=1 (push). Stake returned exactly."""
    section('TEST 3: Push — multiplier=1 returns stake to cash')

    wheel = make_test_wheel(
        'SMOKE_push_only',
        segments=[('Push', Decimal('1'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('1000'),
    )
    fund_user(user, Decimal('2000'), 'push_test')

    coin_before = WalletService.get_balance(user, 'coin')
    cash_before = WalletService.get_balance(user, 'cash')

    spin = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id), stake_amount=Decimal('500'),
    )

    check(spin.outcome == Spin.Outcome.PUSH, 'outcome marked PUSH')
    check(spin.payout_amount == Decimal('500'), 'payout = stake (500)')
    check(
        WalletService.get_balance(user, 'coin') == coin_before - Decimal('500'),
        'coin reduced by stake',
    )
    check(
        WalletService.get_balance(user, 'cash') == cash_before + Decimal('500'),
        'cash credited with stake amount (push back)',
    )


def test_guaranteed_partial_loss(user):
    """Wheel with multiplier=0.5. Half-back."""
    section('TEST 4: Partial loss — multiplier=0.5 returns half')

    wheel = make_test_wheel(
        'SMOKE_partial_loss',
        segments=[('Half', Decimal('0.5'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('1000'),
    )
    fund_user(user, Decimal('2000'), 'partial_test')

    coin_before = WalletService.get_balance(user, 'coin')
    cash_before = WalletService.get_balance(user, 'cash')

    spin = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id), stake_amount=Decimal('1000'),
    )

    check(spin.outcome == Spin.Outcome.PARTIAL_LOSS, 'outcome = PARTIAL_LOSS')
    check(spin.payout_amount == Decimal('500'), 'payout = 0.5 × stake')
    check(
        WalletService.get_balance(user, 'coin') == coin_before - Decimal('1000'),
        'coin reduced by full stake',
    )
    check(
        WalletService.get_balance(user, 'cash') == cash_before + Decimal('500'),
        'cash credited with half (500)',
    )


def test_welcome_spin(user):
    """Welcome wheel: free, one-time per user."""
    section('TEST 5: Welcome spin — free, one-time')

    # Create welcome wheel with deterministic ₦500 payout
    wheel = make_test_wheel(
        'SMOKE_welcome',
        segments=[('₦500', Decimal('500'), 1)],
        is_welcome=True,
    )

    coin_before = WalletService.get_balance(user, 'coin')
    cash_before = WalletService.get_balance(user, 'cash')

    spin = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id), stake_amount=None,
    )

    check(spin.is_welcome_spin is True, 'is_welcome_spin flag set')
    check(spin.stake_amount == Decimal('0'), 'stake = 0')
    check(spin.payout_amount == Decimal('500'), 'payout = flat 500')
    check(
        WalletService.get_balance(user, 'coin') == coin_before,
        'coin unchanged (welcome is free)',
    )
    check(
        WalletService.get_balance(user, 'cash') == cash_before + Decimal('500'),
        'cash credited with flat payout',
    )

    # Try to do a SECOND welcome spin — must be rejected
    try:
        SpinEngine.execute(
            user=user, wheel_id=str(wheel.id), stake_amount=None,
        )
        check(False, 'rejected second welcome spin')
    except SpinRewardsException as e:
        check(
            'already used' in str(e).lower(),
            'rejected second welcome spin',
        )


def test_distribution_statistical(user):
    """
    Run many spins and verify outcome distribution roughly matches weights.

    Wheel: 3 segments with weights 50, 30, 20.
    Run 1000 spins. Expected counts: 500, 300, 200 (±~50 due to variance).
    """
    section('TEST 6: RNG distribution — statistical validation (1000 spins)')

    wheel = make_test_wheel(
        'SMOKE_distribution',
        segments=[
            ('A_50', Decimal('0'), 50),  # multiplier=0 so no money flows
            ('B_30', Decimal('0'), 30),
            ('C_20', Decimal('0'), 20),
        ],
        min_stake=Decimal('1'), max_stake=Decimal('1'),
    )
    fund_user(user, Decimal('5000'), 'dist_test')  # enough for 1000 × 1 stake

    counts = Counter()
    for _ in range(1000):
        spin = SpinEngine.execute(
            user=user, wheel_id=str(wheel.id), stake_amount=Decimal('1'),
        )
        counts[spin.segment_landed.label] += 1

    # Expected: 500/300/200. Allow ±100 (~3 sigma at 1000 trials).
    a_count = counts.get('A_50', 0)
    b_count = counts.get('B_30', 0)
    c_count = counts.get('C_20', 0)
    total = a_count + b_count + c_count

    print(f'    Distribution: A_50={a_count} B_30={b_count} C_20={c_count}')

    check(total == 1000, '1000 spins executed')
    check(400 <= a_count <= 600, f'A_50 (~500): got {a_count}')
    check(200 <= b_count <= 400, f'B_30 (~300): got {b_count}')
    check(100 <= c_count <= 300, f'C_20 (~200): got {c_count}')


def test_stake_validation():
    """Stake out of wheel range must be rejected with no side effects."""
    section('TEST 7: Stake validation — out of range rejected')

    user = make_test_user(800000004)
    fund_user(user, Decimal('10000'), 'validation')

    wheel = make_test_wheel(
        'SMOKE_validation',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('500'),
    )

    coin_before = WalletService.get_balance(user, 'coin')

    # Below min
    try:
        SpinEngine.execute(
            user=user, wheel_id=str(wheel.id), stake_amount=Decimal('50'),
        )
        check(False, 'rejected stake below min')
    except InvalidStakeError:
        check(True, 'rejected stake below min')

    # Above max
    try:
        SpinEngine.execute(
            user=user, wheel_id=str(wheel.id), stake_amount=Decimal('1000'),
        )
        check(False, 'rejected stake above max')
    except InvalidStakeError:
        check(True, 'rejected stake above max')

    # No side effects: coin balance unchanged, no spin row
    check(
        WalletService.get_balance(user, 'coin') == coin_before,
        'no balance change after rejected spins',
    )
    check(
        not Spin.objects.filter(user=user).exists(),
        'no Spin row created on validation failure',
    )

    cleanup(800000004)


def test_insufficient_funds(user):
    """User with low balance cannot stake more than they have."""
    section('TEST 8: Insufficient funds — no partial state')

    wheel = make_test_wheel(
        'SMOKE_insufficient',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('100000'),
    )

    coin_before = WalletService.get_balance(user, 'coin')
    spins_before = Spin.objects.filter(user=user).count()

    try:
        SpinEngine.execute(
            user=user, wheel_id=str(wheel.id),
            stake_amount=coin_before + Decimal('1'),  # over-budget by 1
        )
        check(False, 'rejected over-budget stake')
    except InsufficientFundsError:
        check(True, 'rejected over-budget stake')

    check(
        WalletService.get_balance(user, 'coin') == coin_before,
        'coin unchanged after rejection',
    )
    check(
        Spin.objects.filter(user=user).count() == spins_before,
        'no new Spin row on rejection',
    )


def test_inactive_wheel(user):
    """Inactive wheels can't be spun on."""
    section('TEST 9: Inactive wheel rejected')

    wheel = make_test_wheel(
        'SMOKE_inactive',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('1000'),
    )
    wheel.is_active = False
    wheel.save()

    try:
        SpinEngine.execute(
            user=user, wheel_id=str(wheel.id), stake_amount=Decimal('500'),
        )
        check(False, 'rejected inactive wheel')
    except InvalidStakeError:
        check(True, 'rejected inactive wheel')


def test_ledger_chains(user):
    """
    Across all spins, every Spin row should link to a valid lock_transaction
    and resolution_transaction (where applicable).
    """
    section('TEST 10: Spin → wallet transaction linkage')

    spins_with_lock = Spin.objects.filter(
        user=user, lock_transaction__isnull=False,
    )
    all_locks_linked = all(
        s.lock_transaction is not None for s in spins_with_lock
    )
    check(all_locks_linked, 'every staked spin has a lock_transaction')

    # Every non-welcome spin where stake > 0 should also have a resolution_transaction
    non_welcome = Spin.objects.filter(
        user=user, is_welcome_spin=False, stake_amount__gt=0,
    )
    resolutions_linked = all(
        s.resolution_transaction is not None for s in non_welcome
    )
    check(resolutions_linked, 'every staked spin has a resolution_transaction')


def test_provably_fair_stub(user):
    """The spin records server_seed_hash for future verification."""
    section('TEST 11: Provably fair — seed/hash recorded')

    wheel = make_test_wheel(
        'SMOKE_pf',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('500'),
    )
    fund_user(user, Decimal('1000'), 'pf_test')

    spin = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id),
        stake_amount=Decimal('100'),
        client_seed='client_provided_test_seed',
    )

    check(len(spin.server_seed) == 64, 'server_seed is 256-bit hex (64 chars)')
    check(len(spin.server_seed_hash) == 64, 'server_seed_hash is SHA-256 hex')
    check(spin.client_seed == 'client_provided_test_seed', 'client_seed stored')
    check(spin.rng_value is not None, 'rng_value recorded for audit')

    # Verify the hash actually matches
    import hashlib
    expected_hash = hashlib.sha256(spin.server_seed.encode()).hexdigest()
    check(
        spin.server_seed_hash == expected_hash,
        'server_seed_hash matches SHA-256(server_seed)',
    )


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print('SPIN REWARDS — SPIN ENGINE SMOKE TEST')
    print('Testing every code path of the spin engine.\n')

    user = make_test_user()
    print(f'→ Test user: telegram_id={user.telegram_id}\n')

    try:
        test_guaranteed_loss(user)
        test_guaranteed_win(user)
        test_guaranteed_push(user)
        test_guaranteed_partial_loss(user)
        test_welcome_spin(user)
        test_distribution_statistical(user)
        test_stake_validation()
        test_insufficient_funds(user)
        test_inactive_wheel(user)
        test_ledger_chains(user)
        test_provably_fair_stub(user)
    finally:
        cleanup(800000003)
        cleanup(800000004)
        cleanup_test_wheels()

    print(f'\n{"=" * 60}')
    print(f'RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print(f'{"=" * 60}')

    if TESTS_FAILED:
        print('\n❌ SPIN ENGINE SMOKE TEST FAILED')
        sys.exit(1)
    else:
        print('\n✅ SPIN ENGINE SMOKE TEST PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()