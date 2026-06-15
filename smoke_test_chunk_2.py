"""
Smoke Test — v3 Chunk 2 (Spin Engine — Currency-Aware Routing)
================================================================

Verifies:
  1. Source wallet validation — accepts crypto_coins/naira_coins/bonus_coins
  2. Invalid source_wallet rejected
  3. bonus_destination required when source=bonus_coins
  4. Spin from crypto_coins → win lands in crypto_withdraw at 100%
  5. Spin from naira_coins → win lands in naira_withdraw at 100%
  6. Spin from bonus_coins → naira → 40% × 1 conversion to naira_withdraw
  7. Spin from bonus_coins → crypto → 40% × 1 conversion to crypto_withdraw
  8. Push (mult=1) → stake returns to ORIGIN bucket (per source_wallet)
  9. Loss (mult=0) → stake gone
  10. Spin model has new fields populated correctly
  11. payout_currency + credited_balance set correctly

USAGE:
    docker compose exec api python smoke_test_chunk_2.py
"""
import os
import sys
import uuid
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

# ─── Runner ──────────────────────────────────────────────────────────────
TESTS_RUN = 0
TESTS_PASSED = 0
TESTS_FAILED = 0
FAIL_DETAILS = []


def check(condition, label):
    global TESTS_RUN, TESTS_PASSED, TESTS_FAILED
    TESTS_RUN += 1
    if condition:
        TESTS_PASSED += 1
        print(f'  ✓ {label}')
    else:
        TESTS_FAILED += 1
        FAIL_DETAILS.append(label)
        print(f'  ✗ {label}')


def section(title):
    print(f'\n{"=" * 70}')
    print(f'  {title}')
    print('=' * 70)


# ─── Imports ─────────────────────────────────────────────────────────────
from apps.users.models import User
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService
from apps.spin.models import Spin, Wheel, WheelSegment
from apps.spin.services import SpinEngine
from common.exceptions import InvalidStakeError, InsufficientFundsError

SMOKE_PREFIX = 'v3c2_'
SMOKE_TID = 900200001


def cleanup():
    """Remove all smoke test data."""
    try:
        u = User.objects.get(telegram_id=SMOKE_TID)
        Spin.objects.filter(user=u).delete()
        Transaction.objects.filter(user=u).delete()
        u.delete()
    except User.DoesNotExist:
        pass

    # Remove smoke test wheels (active ones may break wheel range validators
    # for other wheels, so we deactivate any existing ones during the test).
    Wheel.objects.filter(name__startswith=SMOKE_PREFIX).delete()


def make_user():
    return User.objects.create(
        telegram_id=SMOKE_TID,
        first_name='V3C2',
        last_name='SmokeUser',
        username=f'{SMOKE_PREFIX}user_{SMOKE_TID}',
    )


_wheel_counter = [0]

def make_wheel(name, multiplier, is_active=True):
    """Create a single-segment wheel with deterministic outcome.

    Deactivates any other smoke-test wheels first to avoid range overlap.
    """
    # Deactivate any existing smoke-test wheels (range collision)
    Wheel.objects.filter(name__startswith=SMOKE_PREFIX, is_active=True).update(is_active=False)

    _wheel_counter[0] += 1
    unique_name = f'{SMOKE_PREFIX}{name}_{_wheel_counter[0]}'

    wheel = Wheel.objects.create(
        name=unique_name,
        wheel_type='standard',
        min_stake=Decimal('1'),
        max_stake=Decimal('1000000'),
        is_active=False,
        is_welcome_only=False,
        currency_type='coin',
        rtp_target=Decimal('0.95'),
    )
    WheelSegment.objects.create(
        wheel=wheel,
        position=0,
        label=f'{multiplier}x',
        multiplier=Decimal(str(multiplier)),
        probability_weight=1,
        is_active=True,
    )
    if is_active:
        wheel.is_active = True
        wheel.save()
    return wheel


def deactivate_other_wheels():
    """Deactivate any non-smoke-test wheels so range overlap doesn't break tests."""
    others = list(Wheel.objects.exclude(name__startswith=SMOKE_PREFIX)
                  .filter(is_active=True).values_list('id', flat=True))
    Wheel.objects.filter(id__in=others).update(is_active=False)
    return others


def credit_helper(user, balance_type, amount):
    return WalletService.credit(
        user=user,
        amount=Decimal(str(amount)),
        balance_type=balance_type,
        tx_type=Transaction.Type.DEPOSIT,
        reference_id=f'{SMOKE_PREFIX}seed-{uuid.uuid4().hex[:8]}',
    )


# ═════════════════════════════════════════════════════════════════════════
# TESTS
# ═════════════════════════════════════════════════════════════════════════

def test_invalid_source_wallet():
    section('1. Invalid source_wallet rejected')

    user = make_user()
    wheel = make_wheel('invalid_test', 3)

    try:
        SpinEngine.execute(
            user=user, wheel_id=str(wheel.id),
            stake_amount=Decimal('100'),
            source_wallet='earnings',  # invalid
        )
        check(False, "source_wallet='earnings' should be rejected")
    except InvalidStakeError:
        check(True, "source_wallet='earnings' raises InvalidStakeError")
    except Exception as e:
        check(False, f'Wrong exception type: {type(e).__name__}: {e}')

    try:
        SpinEngine.execute(
            user=user, wheel_id=str(wheel.id),
            stake_amount=Decimal('100'),
            source_wallet='deposit_coins',  # deprecated
        )
        check(False, "deprecated 'deposit_coins' should be rejected")
    except InvalidStakeError:
        check(True, "'deposit_coins' (deprecated) raises InvalidStakeError")


def test_bonus_destination_required():
    section('2. bonus_destination required for bonus spins')

    user = User.objects.get(telegram_id=SMOKE_TID)
    credit_helper(user, Transaction.BalanceType.BONUS_COINS, 1000)
    wheel = make_wheel('bonus_dest_test', 3)

    # Without destination → error
    try:
        SpinEngine.execute(
            user=user, wheel_id=str(wheel.id),
            stake_amount=Decimal('100'),
            source_wallet='bonus_coins',
            bonus_destination='',
        )
        check(False, 'bonus spin without destination should raise')
    except InvalidStakeError:
        check(True, 'bonus spin without destination raises InvalidStakeError')

    # Invalid destination → error
    try:
        SpinEngine.execute(
            user=user, wheel_id=str(wheel.id),
            stake_amount=Decimal('100'),
            source_wallet='bonus_coins',
            bonus_destination='banana',
        )
        check(False, 'invalid destination should raise')
    except InvalidStakeError:
        check(True, "destination='banana' raises InvalidStakeError")


def test_crypto_coins_win():
    section('3. Spin from crypto_coins → crypto_withdraw at 100%')

    user = User.objects.get(telegram_id=SMOKE_TID)
    credit_helper(user, Transaction.BalanceType.CRYPTO_COINS, '50')
    wheel = make_wheel('crypto_win', 3)

    before = WalletService.get_wallet_summary(user)
    spin, _ = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id),
        stake_amount=Decimal('10'),
        source_wallet='crypto_coins',
    )
    after = WalletService.get_wallet_summary(user)

    crypto_drop = Decimal(before['crypto_coins']) - Decimal(after['crypto_coins'])
    crypto_w_gain = (Decimal(after['crypto_withdraw_balance'])
                     - Decimal(before['crypto_withdraw_balance']))

    check(crypto_drop == Decimal('10'),
          f'10 crypto_coins debited (got drop={crypto_drop})')
    check(crypto_w_gain == Decimal('30'),
          f'30 USDT to crypto_withdraw at 3x (got gain={crypto_w_gain})')
    check(spin.outcome == 'win', f'outcome=win (got {spin.outcome})')
    check(spin.payout_currency == 'USDT',
          f"payout_currency='USDT' (got {spin.payout_currency!r})")
    check(spin.credited_balance == 'crypto_withdraw',
          f"credited_balance='crypto_withdraw' (got {spin.credited_balance!r})")

    # Naira balances untouched
    naira_change = Decimal(after['naira_coins']) - Decimal(before['naira_coins'])
    naira_w_change = (Decimal(after['naira_withdraw_balance'])
                      - Decimal(before['naira_withdraw_balance']))
    check(naira_change == 0, 'naira_coins untouched')
    check(naira_w_change == 0, 'naira_withdraw untouched')


def test_naira_coins_win():
    section('4. Spin from naira_coins → naira_withdraw at 100%')

    user = User.objects.get(telegram_id=SMOKE_TID)
    credit_helper(user, Transaction.BalanceType.NAIRA_COINS, '5000')
    wheel = make_wheel('naira_win', 3)

    before = WalletService.get_wallet_summary(user)
    spin, _ = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id),
        stake_amount=Decimal('200'),
        source_wallet='naira_coins',
    )
    after = WalletService.get_wallet_summary(user)

    naira_drop = Decimal(before['naira_coins']) - Decimal(after['naira_coins'])
    naira_w_gain = (Decimal(after['naira_withdraw_balance'])
                    - Decimal(before['naira_withdraw_balance']))

    check(naira_drop == Decimal('200'),
          f'200 naira_coins debited (got drop={naira_drop})')
    check(naira_w_gain == Decimal('600'),
          f'600 NGN to naira_withdraw at 3x (got gain={naira_w_gain})')
    check(spin.outcome == 'win', f'outcome=win')
    check(spin.payout_currency == 'NGN',
          f"payout_currency='NGN' (got {spin.payout_currency!r})")
    check(spin.credited_balance == 'naira_withdraw',
          f"credited_balance='naira_withdraw' (got {spin.credited_balance!r})")


def test_bonus_to_naira_win():
    section('5. Bonus → naira → 40% × 1 = naira_withdraw')

    user = User.objects.get(telegram_id=SMOKE_TID)
    # Top up bonus
    credit_helper(user, Transaction.BalanceType.BONUS_COINS, '500')
    wheel = make_wheel('bonus_to_naira', 3)

    before = WalletService.get_wallet_summary(user)
    spin, _ = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id),
        stake_amount=Decimal('100'),
        source_wallet='bonus_coins',
        bonus_destination='naira',
    )
    after = WalletService.get_wallet_summary(user)

    bonus_drop = Decimal(before['bonus_coins']) - Decimal(after['bonus_coins'])
    naira_w_gain = (Decimal(after['naira_withdraw_balance'])
                    - Decimal(before['naira_withdraw_balance']))

    # Math: 100 stake × 3 = 300 gross × 0.40 × 1 = 120 NGN
    check(bonus_drop == Decimal('100'),
          f'100 bonus_coins debited (got drop={bonus_drop})')
    check(naira_w_gain == Decimal('120'),
          f'120 NGN to naira_withdraw (300 × 0.40 × 1) (got gain={naira_w_gain})')
    check(spin.bonus_destination == 'naira',
          f"bonus_destination='naira' (got {spin.bonus_destination!r})")
    check(spin.payout_currency == 'NGN',
          f"payout_currency='NGN' (got {spin.payout_currency!r})")


def test_bonus_to_crypto_win():
    section('6. Bonus → crypto → 40% × 1 = crypto_withdraw')

    user = User.objects.get(telegram_id=SMOKE_TID)
    credit_helper(user, Transaction.BalanceType.BONUS_COINS, '500')
    wheel = make_wheel('bonus_to_crypto', 3)

    before = WalletService.get_wallet_summary(user)
    spin, _ = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id),
        stake_amount=Decimal('100'),
        source_wallet='bonus_coins',
        bonus_destination='crypto',
    )
    after = WalletService.get_wallet_summary(user)

    bonus_drop = Decimal(before['bonus_coins']) - Decimal(after['bonus_coins'])
    crypto_w_gain = (Decimal(after['crypto_withdraw_balance'])
                     - Decimal(before['crypto_withdraw_balance']))

    # Math: 100 stake × 3 = 300 gross × 0.40 × 1 = 120 USDT
    check(bonus_drop == Decimal('100'),
          f'100 bonus_coins debited (got drop={bonus_drop})')
    check(crypto_w_gain == Decimal('120'),
          f'120 USDT to crypto_withdraw (got gain={crypto_w_gain})')
    check(spin.bonus_destination == 'crypto',
          f"bonus_destination='crypto'")
    check(spin.payout_currency == 'USDT',
          f"payout_currency='USDT'")
    check(spin.credited_balance == 'crypto_withdraw',
          f"credited_balance='crypto_withdraw'")


def test_push():
    section('7. Push (mult=1) — stake returns to origin bucket')

    user = User.objects.get(telegram_id=SMOKE_TID)
    credit_helper(user, Transaction.BalanceType.CRYPTO_COINS, '50')

    wheel = make_wheel('push_test', 1)

    before = WalletService.get_wallet_summary(user)
    spin, _ = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id),
        stake_amount=Decimal('10'),
        source_wallet='crypto_coins',
    )
    after = WalletService.get_wallet_summary(user)

    crypto_change = Decimal(after['crypto_coins']) - Decimal(before['crypto_coins'])
    crypto_w_change = (Decimal(after['crypto_withdraw_balance'])
                       - Decimal(before['crypto_withdraw_balance']))

    check(crypto_change == 0,
          f'Push: crypto_coins unchanged (delta={crypto_change})')
    check(crypto_w_change == 0,
          f'Push: crypto_withdraw unchanged (delta={crypto_w_change})')
    check(spin.outcome == 'push', f'outcome=push')


def test_loss():
    section('8. Loss (mult=0) — stake gone, no credit anywhere')

    user = User.objects.get(telegram_id=SMOKE_TID)
    credit_helper(user, Transaction.BalanceType.NAIRA_COINS, '500')
    wheel = make_wheel('loss_test', 0)

    before = WalletService.get_wallet_summary(user)
    spin, _ = SpinEngine.execute(
        user=user, wheel_id=str(wheel.id),
        stake_amount=Decimal('100'),
        source_wallet='naira_coins',
    )
    after = WalletService.get_wallet_summary(user)

    naira_drop = Decimal(before['naira_coins']) - Decimal(after['naira_coins'])
    naira_w_change = (Decimal(after['naira_withdraw_balance'])
                      - Decimal(before['naira_withdraw_balance']))

    check(naira_drop == Decimal('100'),
          f'Loss: 100 naira_coins gone (drop={naira_drop})')
    check(naira_w_change == 0,
          f'Loss: naira_withdraw unchanged (delta={naira_w_change})')
    check(spin.outcome == 'loss', f'outcome=loss')
    check(spin.payout_currency == '',
          f"payout_currency='' for loss (got {spin.payout_currency!r})")


def test_insufficient_funds():
    section('9. Insufficient funds across all wallets')

    user = User.objects.get(telegram_id=SMOKE_TID)
    wheel = make_wheel('insuff_test', 3)

    # Try to spin way more than we have from crypto_coins
    try:
        SpinEngine.execute(
            user=user, wheel_id=str(wheel.id),
            stake_amount=Decimal('999999'),
            source_wallet='crypto_coins',
        )
        check(False, 'Should have raised InsufficientFundsError')
    except InsufficientFundsError:
        check(True, 'crypto_coins overspend → InsufficientFundsError')


# ═════════════════════════════════════════════════════════════════════════
# RUNNER
# ═════════════════════════════════════════════════════════════════════════

def main():
    print('=' * 70)
    print('  SPIN REWARDS v3 — CHUNK 2 SMOKE TEST (SPIN ENGINE)')
    print('=' * 70)

    cleanup()
    deactivated = deactivate_other_wheels()
    if deactivated:
        print(f'[setup] Temporarily deactivated {len(deactivated)} non-test wheels')

    try:
        test_invalid_source_wallet()
        test_bonus_destination_required()
        test_crypto_coins_win()
        test_naira_coins_win()
        test_bonus_to_naira_win()
        test_bonus_to_crypto_win()
        test_push()
        test_loss()
        test_insufficient_funds()
    finally:
        cleanup()
        # Restore the wheels we deactivated
        if deactivated:
            Wheel.objects.filter(id__in=deactivated).update(is_active=True)
            print(f'[teardown] Restored {len(deactivated)} wheels to active')

    print(f'\n{"=" * 70}')
    print(f'  RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print('=' * 70)

    if TESTS_FAILED:
        print('\nFAILED:')
        for label in FAIL_DETAILS:
            print(f'  ✗ {label}')
        print('\n❌ CHUNK 2 FAILED — fix before proceeding to Chunk 3')
        sys.exit(1)
    else:
        print('\n✅ CHUNK 2 GREEN — ready for Chunk 3 (deposits routing)')
        sys.exit(0)


if __name__ == '__main__':
    main()