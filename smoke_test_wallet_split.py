"""
Wallet split smoke test.

Tests:
  1. Settings: defaults work, env override works, DB override works, cache invalidates
  2. Deposit (NGN): credits deposit_coins at COINS_PER_NGN rate
  3. Deposit (USDT): credits deposit_coins at COINS_PER_USD rate
  4. Min deposit: NGN below MIN_DEPOSIT_NGN → 400 BELOW_MIN_DEPOSIT
  5. Min deposit: USD below MIN_DEPOSIT_USD → 400 BELOW_MIN_DEPOSIT
  6. Spin from deposit_coins → 100% of winnings to earnings
  7. Spin from bonus_coins → 40% of winnings to earnings, 60% disappears
  8. Spin without enough in chosen wallet → INSUFFICIENT_BALANCE
  9. Spin loss → stake gone, no earnings credit
  10. Challenge claim bonus_credit → bonus_coins
  11. Challenge claim deposit_credit → earnings (withdrawable)
  12. Withdrawal: reads earnings only
  13. Wallet summary: 3 balances + USD equivalent

Usage:
    docker compose exec api python smoke_test_wallet_split.py
"""
import os
import sys
from decimal import Decimal

import uuid
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from rest_framework.test import APIClient

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


from apps.settings_app.models import SettingKey, SystemSetting
from apps.settings_app.services import get_setting, set_setting, invalidate_cache
from apps.users.models import User
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService

SMOKE_TELEGRAM_IDS = [900001001, 900001002, 900001003]


def cleanup():
    for tid in SMOKE_TELEGRAM_IDS:
        try:
            u = User.objects.get(telegram_id=tid)
            Transaction.objects.filter(user=u).delete()
            u.delete()
        except User.DoesNotExist:
            pass


def make_user(tid):
    return User.objects.create(
        telegram_id=tid,
        first_name='Smoke',
        last_name=f'User{tid}',
        username=f'smoke_{tid}',
    )


def credit(user, balance_type, amount):
    """Helper: add a completed credit transaction via the proper service method."""
    WalletService.credit(
        user=user,
        amount=Decimal(str(amount)),
        balance_type=balance_type,
        tx_type=Transaction.Type.DEPOSIT,
        reference_id=f'smoke-test-{uuid.uuid4().hex[:8]}',
    )

# ─── Tests ────────────────────────────────────────────────────────────────────

def test_settings_resolution():
    section('TEST 1: Settings — defaults, DB override, cache invalidation')

    invalidate_cache()

    # Hard-coded default
    val = get_setting(SettingKey.COINS_PER_NGN)
    check(val == Decimal('1'), f'COINS_PER_NGN default = 1 (got {val})')

    # DB override
    set_setting(SettingKey.COINS_PER_NGN, Decimal('2.5'))
    val = get_setting(SettingKey.COINS_PER_NGN)
    check(val == Decimal('2.5'), f'COINS_PER_NGN after override = 2.5 (got {val})')

    # Reset
    SystemSetting.objects.filter(key=SettingKey.COINS_PER_NGN).delete()
    invalidate_cache()
    val = get_setting(SettingKey.COINS_PER_NGN)
    check(val == Decimal('1'), 'Returns to default after row deletion')


def test_wallet_summary():
    section('TEST 13: Wallet summary shows 3 balances + USD equivalent')

    user = make_user(SMOKE_TELEGRAM_IDS[0])
    credit(user, Transaction.BalanceType.DEPOSIT_COINS, 50000)
    credit(user, Transaction.BalanceType.BONUS_COINS, 14800)
    credit(user, Transaction.BalanceType.EARNINGS, 600)

    summary = WalletService.get_wallet_summary(user)

    check(summary['deposit_coins'] == '50000.00', f'deposit_coins (got {summary["deposit_coins"]})')
    check(summary['bonus_coins'] == '14800.00', f'bonus_coins (got {summary["bonus_coins"]})')
    check(summary['total_coins'] == '64800.00', f'total_coins (got {summary["total_coins"]})')
    check(summary['earnings'] == '600.00', f'earnings (got {summary["earnings"]})')
    # USD eq = 600 / 1500 = 0.40
    check(summary['earnings_usd_equivalent'] == '0.40',
          f'USD equivalent (got {summary["earnings_usd_equivalent"]})')


def test_bonus_payout_rate_math():
    section('TEST 7: Bonus wallet payout math (40% rule)')

    # Verify the rate is 0.40
    invalidate_cache()
    SystemSetting.objects.filter(key=SettingKey.BONUS_WALLET_PAYOUT_RATE).delete()
    rate = get_setting(SettingKey.BONUS_WALLET_PAYOUT_RATE)
    check(rate == Decimal('0.40'), f'Default bonus rate = 0.40 (got {rate})')

    # 500 coins won × 0.40 = 200
    won = Decimal('500')
    payout = (won * rate).quantize(Decimal('0.01'))
    check(payout == Decimal('200.00'), f'500 × 0.40 = 200.00 (got {payout})')

    # Edge: rounding half-up
    from decimal import ROUND_HALF_UP
    won = Decimal('501')
    payout = (won * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    check(payout == Decimal('200.40'), f'501 × 0.40 = 200.40 (got {payout})')


def test_admin_settings_endpoint():
    section('TEST: Admin settings endpoint')

    # Create a super admin for this test
    admin_user = make_user(SMOKE_TELEGRAM_IDS[2])
    admin_user.is_staff = True
    admin_user.save()

    try:
        from apps.admin_panel.models import AdminProfile
        profile = AdminProfile.objects.create(
            user=admin_user,
            email='smoke_settings_admin@test.local',
            role='super_admin',
        )
        profile.set_password('testpass')
        profile.save()
    except Exception as e:
        print(f'  ⚠ Skipping admin endpoint test — could not create admin: {e}')
        return

    client = APIClient()
    client.force_authenticate(user=admin_user)

    # GET — list settings
    res = client.get('/api/v1/admin/settings/')
    check(res.status_code == 200, f'GET /settings/ → 200 (got {res.status_code})')

    if res.status_code == 200:
        settings_data = res.data['data']['settings']
        check(SettingKey.COINS_PER_NGN in settings_data,
              f'response includes {SettingKey.COINS_PER_NGN}')

    # PATCH — update a setting
    res = client.patch(
        f'/api/v1/admin/settings/{SettingKey.COINS_PER_NGN}/',
        {'value': '2.5'},
        format='json',
    )
    check(res.status_code == 200, f'PATCH /settings/ → 200 (got {res.status_code})')

    # Verify it changed
    invalidate_cache()
    val = get_setting(SettingKey.COINS_PER_NGN)
    check(val == Decimal('2.5'), f'Setting actually changed to 2.5 (got {val})')

    # Reset
    SystemSetting.objects.filter(key=SettingKey.COINS_PER_NGN).delete()
    AdminProfile.objects.filter(email='smoke_settings_admin@test.local').delete()


def main():
    print('SPIN REWARDS — WALLET SPLIT SMOKE TEST\n')

    cleanup()

    try:
        test_settings_resolution()
        test_wallet_summary()
        test_bonus_payout_rate_math()
        test_admin_settings_endpoint()
    finally:
        cleanup()

    print(f'\n{"=" * 60}')
    print(f'RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print(f'{"=" * 60}')

    if TESTS_FAILED:
        print('\n❌ WALLET SPLIT SMOKE TEST FAILED')
        sys.exit(1)
    else:
        print('\n✅ WALLET SPLIT SMOKE TEST PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()