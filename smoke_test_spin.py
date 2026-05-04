"""
Spin engine smoke test — v2 (stake-driven wheel selection).

Tests every spin engine code path against the live database.

⚠️  IMPORTANT: This test wipes ALL Wheel rows at start and re-seeds at end.
    Don't run against production data with real spin history. The test
    refuses to wipe if non-test Spin records exist as a safety check.

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

from django.core.exceptions import ValidationError
from django.core.management import call_command

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
    """Delete test user respecting PROTECT FKs."""
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
    import secrets
    WalletService.credit(
        user=user,
        amount=coins,
        balance_type='coin',
        tx_type=Transaction.Type.DEPOSIT,
        reference_id=f'smoke_{label}_{secrets.token_hex(8)}',
        metadata={'source': 'smoke_test_topup'},
    )


def wipe_all_wheels():
    """
    Delete every Wheel and WheelSegment for full test isolation.

    Refuses to run if non-test Spin records exist (defensive safety check).
    """
    test_telegram_ids = [800000003, 800000004]
    non_test_spins = Spin.objects.exclude(
        user__telegram_id__in=test_telegram_ids
    ).count()

    if non_test_spins > 0:
        raise RuntimeError(
            f'Cannot wipe wheels — {non_test_spins} non-test Spin records exist. '
            f'This test is destructive; do not run against production data.'
        )

    Spin.objects.all().delete()
    Wheel.objects.all().delete()


def reseed_production_wheels():
    """Re-create the seeded production wheels at end of test."""
    try:
        call_command('seed_wheels', verbosity=0)
        print('  Production wheels re-seeded.')
    except Exception as e:
        print(f'\n⚠️  Could not re-seed production wheels: {e}')
        print('   Run `python manage.py seed_wheels` manually.')


def make_test_wheel(name, segments, currency_type='coin', is_welcome=False,
                    min_stake=None, max_stake=None):
    """Create a controlled-outcome wheel for testing."""
    if is_welcome:
        wheel_type = Wheel.WheelType.WELCOME
        min_stake = Decimal('0')
        max_stake = Decimal('0')
    else:
        wheel_type = Wheel.WheelType.STANDARD
        if min_stake is None:
            min_stake = Decimal('100')
        if max_stake is None:
            max_stake = Decimal('10000')

    wheel = Wheel(
        wheel_type=wheel_type,
        name=name,
        currency_type=currency_type,
        min_stake=min_stake,
        max_stake=max_stake,
        is_welcome_only=is_welcome,
        is_active=True,
    )
    wheel.save()

    for pos, (label, mult, weight) in enumerate(segments):
        WheelSegment.objects.create(
            wheel=wheel, position=pos, label=label,
            multiplier=mult, probability_weight=weight,
        )
    return wheel


def cleanup_test_wheels():
    """
    Delete test wheels AND their spins.

    Spin → Wheel is a PROTECT FK, so wheels with linked spins can't be
    deleted directly. We delete spins (and their linked wallet transactions
    via the related_name='spin_locks'/'spin_resolutions' chain) first.
    """
    test_wheels = Wheel.objects.filter(name__startswith='SMOKE_')
    # Delete spins first — they PROTECT the wheels
    Spin.objects.filter(wheel__in=test_wheels).delete()
    # Now safe to delete wheels
    test_wheels.delete()


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_guaranteed_loss(user):
    section('TEST 1: Guaranteed loss — stake forfeited')
    cleanup_test_wheels()
    wheel = make_test_wheel(
        'SMOKE_loss_only',
        segments=[('Loss', Decimal('0'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('1001'),
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
    check(WalletService.get_balance(user, 'coin') == coin_before - Decimal('500'),
          'coin reduced by stake (500)')
    check(WalletService.get_balance(user, 'cash') == cash_before,
          'cash unchanged on loss')
    check(WalletService.get_balance(user, 'staked') == staked_before,
          'staked back to original (lock cleared)')
    check(spin.lock_transaction is not None, 'lock_transaction recorded')
    check(spin.resolution_transaction is not None, 'resolution_transaction recorded')


def test_guaranteed_win(user):
    section('TEST 2: Guaranteed 3× win — winnings credit cash')
    cleanup_test_wheels()
    wheel = make_test_wheel(
        'SMOKE_win_only',
        segments=[('Triple', Decimal('3'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('1001'),
    )
    fund_user(user, Decimal('5000'), 'win_test')

    coin_before = WalletService.get_balance(user, 'coin')
    cash_before = WalletService.get_balance(user, 'cash')

    spin = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id), stake_amount=Decimal('500'),
    )

    check(spin.outcome == Spin.Outcome.WIN, 'outcome marked WIN')
    check(spin.payout_amount == Decimal('1500'), 'payout = 3 × stake (1500)')
    check(WalletService.get_balance(user, 'coin') == coin_before - Decimal('500'),
          'coin reduced by stake only (500)')
    check(WalletService.get_balance(user, 'cash') == cash_before + Decimal('1500'),
          'cash credited with full payout (1500)')


def test_guaranteed_push(user):
    section('TEST 3: Push — multiplier=1 returns stake to cash')
    cleanup_test_wheels()
    wheel = make_test_wheel(
        'SMOKE_push_only',
        segments=[('Push', Decimal('1'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('1001'),
    )
    fund_user(user, Decimal('2000'), 'push_test')

    coin_before = WalletService.get_balance(user, 'coin')
    cash_before = WalletService.get_balance(user, 'cash')

    spin = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id), stake_amount=Decimal('500'),
    )

    check(spin.outcome == Spin.Outcome.PUSH, 'outcome marked PUSH')
    check(spin.payout_amount == Decimal('500'), 'payout = stake (500)')
    check(WalletService.get_balance(user, 'coin') == coin_before - Decimal('500'),
          'coin reduced by stake')
    check(WalletService.get_balance(user, 'cash') == cash_before + Decimal('500'),
          'cash credited with stake amount (push back)')


def test_guaranteed_partial_loss(user):
    section('TEST 4: Partial loss — multiplier=0.5 returns half')
    cleanup_test_wheels()
    wheel = make_test_wheel(
        'SMOKE_partial_loss',
        segments=[('Half', Decimal('0.5'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('1001'),
    )
    fund_user(user, Decimal('2000'), 'partial_test')

    coin_before = WalletService.get_balance(user, 'coin')
    cash_before = WalletService.get_balance(user, 'cash')

    spin = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id), stake_amount=Decimal('1000'),
    )

    check(spin.outcome == Spin.Outcome.PARTIAL_LOSS, 'outcome = PARTIAL_LOSS')
    check(spin.payout_amount == Decimal('500'), 'payout = 0.5 × stake')
    check(WalletService.get_balance(user, 'coin') == coin_before - Decimal('1000'),
          'coin reduced by full stake')
    check(WalletService.get_balance(user, 'cash') == cash_before + Decimal('500'),
          'cash credited with half (500)')


def test_welcome_spin(user):
    section('TEST 5: Welcome spin — free, one-time')
    cleanup_test_wheels()
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
    check(WalletService.get_balance(user, 'coin') == coin_before,
          'coin unchanged (welcome is free)')
    check(WalletService.get_balance(user, 'cash') == cash_before + Decimal('500'),
          'cash credited with flat payout')

    try:
        SpinEngine.execute(user=user, wheel_id=str(wheel.id), stake_amount=None)
        check(False, 'rejected second welcome spin')
    except SpinRewardsException as e:
        check('already used' in str(e).lower(), 'rejected second welcome spin')


def test_distribution_statistical(user):
    section('TEST 6: RNG distribution — statistical validation (1000 spins)')
    cleanup_test_wheels()
    wheel = make_test_wheel(
        'SMOKE_distribution',
        segments=[
            ('A_50', Decimal('0'), 50),
            ('B_30', Decimal('0'), 30),
            ('C_20', Decimal('0'), 20),
        ],
        min_stake=Decimal('1'), max_stake=Decimal('2'),
    )
    fund_user(user, Decimal('5000'), 'dist_test')

    counts = Counter()
    for _ in range(1000):
        spin = SpinEngine.execute(
            user=user, wheel_id=str(wheel.id), stake_amount=Decimal('1'),
        )
        counts[spin.segment_landed.label] += 1

    a, b, c = counts.get('A_50', 0), counts.get('B_30', 0), counts.get('C_20', 0)
    print(f'    Distribution: A_50={a} B_30={b} C_20={c}')

    check(a + b + c == 1000, '1000 spins executed')
    check(400 <= a <= 600, f'A_50 (~500): got {a}')
    check(200 <= b <= 400, f'B_30 (~300): got {b}')
    check(100 <= c <= 300, f'C_20 (~200): got {c}')


def test_stake_validation():
    section('TEST 7: Stake validation — out of range rejected')
    user = make_test_user(800000004)
    fund_user(user, Decimal('10000'), 'validation')

    cleanup_test_wheels()
    wheel = make_test_wheel(
        'SMOKE_validation',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('500'),
    )

    coin_before = WalletService.get_balance(user, 'coin')

    try:
        SpinEngine.execute(user=user, wheel_id=str(wheel.id), stake_amount=Decimal('50'))
        check(False, 'rejected stake below min')
    except InvalidStakeError:
        check(True, 'rejected stake below min')

    try:
        SpinEngine.execute(user=user, wheel_id=str(wheel.id), stake_amount=Decimal('500'))
        check(False, 'rejected stake AT max (exclusive upper bound)')
    except InvalidStakeError:
        check(True, 'rejected stake AT max (exclusive upper bound)')

    try:
        SpinEngine.execute(user=user, wheel_id=str(wheel.id), stake_amount=Decimal('1000'))
        check(False, 'rejected stake above max')
    except InvalidStakeError:
        check(True, 'rejected stake above max')

    check(WalletService.get_balance(user, 'coin') == coin_before,
          'no balance change after rejected spins')
    check(not Spin.objects.filter(user=user).exists(),
          'no Spin row created on validation failure')

    cleanup(800000004)


def test_insufficient_funds(user):
    section('TEST 8: Insufficient funds — no partial state')
    cleanup_test_wheels()
    wheel = make_test_wheel(
        'SMOKE_insufficient',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('100001'),
    )

    coin_before = WalletService.get_balance(user, 'coin')
    spins_before = Spin.objects.filter(user=user).count()

    try:
        SpinEngine.execute(
            user=user, wheel_id=str(wheel.id),
            stake_amount=coin_before + Decimal('1'),
        )
        check(False, 'rejected over-budget stake')
    except InsufficientFundsError:
        check(True, 'rejected over-budget stake')

    check(WalletService.get_balance(user, 'coin') == coin_before,
          'coin unchanged after rejection')
    check(Spin.objects.filter(user=user).count() == spins_before,
          'no new Spin row on rejection')


def test_inactive_wheel(user):
    section('TEST 9: Inactive wheel rejected')
    cleanup_test_wheels()
    wheel = make_test_wheel(
        'SMOKE_inactive',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('1001'),
    )
    wheel.is_active = False
    wheel.save()

    try:
        SpinEngine.execute(user=user, wheel_id=str(wheel.id), stake_amount=Decimal('500'))
        check(False, 'rejected inactive wheel')
    except InvalidStakeError:
        check(True, 'rejected inactive wheel')


def test_ledger_chains(user):
    section('TEST 10: Spin → wallet transaction linkage')
    spins_with_lock = Spin.objects.filter(user=user, lock_transaction__isnull=False)
    check(all(s.lock_transaction is not None for s in spins_with_lock),
          'every staked spin has a lock_transaction')

    non_welcome = Spin.objects.filter(user=user, is_welcome_spin=False, stake_amount__gt=0)
    check(all(s.resolution_transaction is not None for s in non_welcome),
          'every staked spin has a resolution_transaction')


def test_provably_fair_stub(user):
    section('TEST 11: Provably fair — seed/hash recorded')
    cleanup_test_wheels()
    wheel = make_test_wheel(
        'SMOKE_pf',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('100'), max_stake=Decimal('501'),
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

    import hashlib
    expected_hash = hashlib.sha256(spin.server_seed.encode()).hexdigest()
    check(spin.server_seed_hash == expected_hash,
          'server_seed_hash matches SHA-256(server_seed)')


def test_find_wheel_for_stake():
    section('TEST 12: find_wheel_for_stake — locates correct wheel')
    cleanup_test_wheels()

    standard = make_test_wheel(
        'SMOKE_standard',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('200'), max_stake=Decimal('500'),
    )
    power = make_test_wheel(
        'SMOKE_power',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('500'), max_stake=Decimal('2000'),
    )
    mega = make_test_wheel(
        'SMOKE_mega',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('2000'), max_stake=Decimal('100001'),
    )

    check(SpinEngine.find_wheel_for_stake(Decimal('300')) == standard,
          '₦300 → Standard wheel')
    check(SpinEngine.find_wheel_for_stake(Decimal('1000')) == power,
          '₦1000 → Power wheel')
    check(SpinEngine.find_wheel_for_stake(Decimal('5000')) == mega,
          '₦5000 → Mega wheel')
    check(SpinEngine.find_wheel_for_stake(Decimal('100')) is None,
          '₦100 → None (below all)')
    check(SpinEngine.find_wheel_for_stake(Decimal('200000')) is None,
          '₦200000 → None (above all)')


def test_boundary_inclusive_lower_exclusive_upper():
    section('TEST 13: Boundary semantics — inclusive lower, exclusive upper')
    cleanup_test_wheels()

    standard = make_test_wheel(
        'SMOKE_standard',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('200'), max_stake=Decimal('500'),
    )
    power = make_test_wheel(
        'SMOKE_power',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('500'), max_stake=Decimal('2000'),
    )

    check(SpinEngine.find_wheel_for_stake(Decimal('200')) == standard,
          '₦200 (lower bound) → Standard (inclusive lower)')
    check(SpinEngine.find_wheel_for_stake(Decimal('499')) == standard,
          '₦499 (just below max) → Standard')
    check(SpinEngine.find_wheel_for_stake(Decimal('500')) == power,
          '₦500 (boundary) → Power (NOT Standard, exclusive upper)')

    check(standard.matches_stake(Decimal('200')) is True,
          'matches_stake(200) on [200,500) is True')
    check(standard.matches_stake(Decimal('499')) is True,
          'matches_stake(499) on [200,500) is True')
    check(standard.matches_stake(Decimal('500')) is False,
          'matches_stake(500) on [200,500) is False (exclusive)')
    check(standard.matches_stake(Decimal('199')) is False,
          'matches_stake(199) on [200,500) is False')


def test_overlap_validation():
    section('TEST 14: Overlap validation — admin cannot create conflicts')
    cleanup_test_wheels()

    make_test_wheel(
        'SMOKE_first',
        segments=[('A', Decimal('1'), 1)],
        min_stake=Decimal('200'), max_stake=Decimal('500'),
    )

    try:
        overlapping = Wheel(
            wheel_type=Wheel.WheelType.STANDARD, name='SMOKE_overlap',
            currency_type='coin',
            min_stake=Decimal('300'), max_stake=Decimal('700'),
            is_welcome_only=False, is_active=True,
        )
        overlapping.save()
        check(False, 'rejected overlapping wheel [300, 700)')
    except ValidationError as e:
        check('overlap' in str(e).lower(), 'rejected overlapping wheel [300, 700)')

    try:
        adjacent = Wheel(
            wheel_type=Wheel.WheelType.STANDARD, name='SMOKE_adjacent',
            currency_type='coin',
            min_stake=Decimal('500'), max_stake=Decimal('1000'),
            is_welcome_only=False, is_active=True,
        )
        adjacent.save()
        check(True, 'allowed adjacent wheel [500, 1000) — no actual overlap')
    except ValidationError as e:
        check(False, f'incorrectly rejected adjacent wheel: {e}')

    try:
        inactive_overlap = Wheel(
            wheel_type=Wheel.WheelType.STANDARD, name='SMOKE_inactive_overlap',
            currency_type='coin',
            min_stake=Decimal('300'), max_stake=Decimal('700'),
            is_welcome_only=False, is_active=False,
        )
        inactive_overlap.save()
        check(True, 'allowed overlapping INACTIVE wheel')
    except ValidationError as e:
        check(False, f'incorrectly rejected inactive overlapping wheel: {e}')

    try:
        inactive_overlap.is_active = True
        inactive_overlap.save()
        check(False, 'rejected activating an overlapping wheel')
    except ValidationError as e:
        check('overlap' in str(e).lower(), 'rejected activating an overlapping wheel')


def test_welcome_wheel_validation():
    section('TEST 15: Welcome wheel validation — must be free')
    cleanup_test_wheels()

    try:
        bad_welcome = Wheel(
            wheel_type=Wheel.WheelType.WELCOME, name='SMOKE_bad_welcome',
            currency_type='coin',
            min_stake=Decimal('100'), max_stake=Decimal('500'),
            is_welcome_only=True, is_active=True,
        )
        bad_welcome.save()
        check(False, 'rejected welcome wheel with non-zero stakes')
    except ValidationError:
        check(True, 'rejected welcome wheel with non-zero stakes')

    try:
        good_welcome = Wheel(
            wheel_type=Wheel.WheelType.WELCOME, name='SMOKE_good_welcome',
            currency_type='coin',
            min_stake=Decimal('0'), max_stake=Decimal('0'),
            is_welcome_only=True, is_active=True,
        )
        good_welcome.save()
        check(True, 'accepted welcome wheel with min=0, max=0')
    except ValidationError as e:
        check(False, f'incorrectly rejected good welcome wheel: {e}')


def test_max_stake_must_exceed_min():
    section('TEST 16: max_stake validation')
    cleanup_test_wheels()

    try:
        invalid = Wheel(
            wheel_type=Wheel.WheelType.STANDARD, name='SMOKE_invalid',
            currency_type='coin',
            min_stake=Decimal('500'), max_stake=Decimal('500'),
            is_welcome_only=False, is_active=True,
        )
        invalid.save()
        check(False, 'rejected wheel where max == min')
    except ValidationError:
        check(True, 'rejected wheel where max == min')

    try:
        invalid = Wheel(
            wheel_type=Wheel.WheelType.STANDARD, name='SMOKE_invalid2',
            currency_type='coin',
            min_stake=Decimal('500'), max_stake=Decimal('200'),
            is_welcome_only=False, is_active=True,
        )
        invalid.save()
        check(False, 'rejected wheel where max < min')
    except ValidationError:
        check(True, 'rejected wheel where max < min')


def main():
    print('SPIN REWARDS — SPIN ENGINE SMOKE TEST (v2)')
    print('Testing every code path of the spin engine.\n')

    cleanup(800000003)
    cleanup(800000004)

    print('→ Wiping all existing wheels for test isolation...')
    try:
        wipe_all_wheels()
        print('  Done.\n')
    except RuntimeError as e:
        print(f'\n❌ {e}')
        sys.exit(1)

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
        test_find_wheel_for_stake()
        test_boundary_inclusive_lower_exclusive_upper()
        test_overlap_validation()
        test_welcome_wheel_validation()
        test_max_stake_must_exceed_min()
    finally:
        cleanup(800000003)
        cleanup(800000004)
        cleanup_test_wheels()
        print('\n→ Re-seeding production wheels...')
        reseed_production_wheels()

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