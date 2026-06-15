"""
Smoke Test — v3 Chunk 6 (Admin Endpoints) — Clean Rewrite
============================================================

Verifies:
  1. AdminUserDetailView returns v3 wallet shape (5 balances + staked)
  2. AdminUserDetailView includes bank_accounts + crypto_wallets arrays
  3. AdminUserDetailView does NOT have v2 keys (deposit_coins, total_coins, earnings)
  4. AdminWithdrawalsListView returns rail + currency per row
  5. AdminWithdrawalsListView overview splits by currency (ngn, usdt blocks)
  6. AdminWithdrawalsListView filter by ?rail=crypto works
  7. Settings: v3 keys present, no COINS_PER_*
  8. ROLE_PERMISSIONS has edit_settings + view_settings
  9. Setting can be updated end-to-end

USAGE:
    docker compose exec api python smoke_test_chunk_6.py
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
from django.utils import timezone
from rest_framework.test import APIRequestFactory
from rest_framework.request import Request

from apps.users.models import User
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService
from apps.withdrawals.models import Withdrawal, CryptoWallet
from apps.withdrawals.services import WithdrawalService
from apps.kyc.models import BankAccount, KYCProfile
from apps.settings_app.services import set_setting, invalidate_cache, get_setting
from apps.settings_app.models import SettingKey
from apps.admin_panel.models import AdminProfile, ROLE_PERMISSIONS

SMOKE_PREFIX = 'v3c6_'
SMOKE_TID = 900600001
ADMIN_TID = 900600099
VALID_TRC20 = 'TS4mw7FvDqCWBvMJonTJGwRUxuP7s3R7NH'


def cleanup():
    for tid in [SMOKE_TID, ADMIN_TID]:
        try:
            u = User.objects.get(telegram_id=tid)
            Withdrawal.objects.filter(user=u).delete()
            CryptoWallet.objects.filter(user=u).delete()
            BankAccount.objects.filter(user=u).delete()
            KYCProfile.objects.filter(user=u).delete()
            Transaction.objects.filter(user=u).delete()
            try:
                AdminProfile.objects.filter(user=u).delete()
            except Exception:
                pass
            u.delete()
        except User.DoesNotExist:
            pass


def build_request(path, user, method='GET'):
    """
    Build a DRF Request that views expect. Plain RequestFactory creates a
    Django WSGIRequest which has no .query_params; wrapping with Request
    gives us the DRF object views expect.
    """
    factory = APIRequestFactory()
    if method == 'GET':
        django_request = factory.get(path)
    elif method == 'POST':
        django_request = factory.post(path)
    else:
        django_request = factory.generic(method, path)

    drf_request = Request(django_request)
    drf_request.user = user
    return drf_request


def make_user_with_balances():
    user = User.objects.create(
        telegram_id=SMOKE_TID,
        first_name='V3C6',
        last_name='SmokeUser',
        username=f'{SMOKE_PREFIX}user',
    )

    try:
        KYCProfile.objects.create(
            user=user,
            full_name='V3C6 SMOKE',
            nin='12345678901',
            date_of_birth='1990-01-01',
            personal_info_status='verified',
            document_status='verified',
            bank_account_status='verified',
            verified_at=timezone.now(),
        )
    except Exception:
        profile = KYCProfile.objects.create(user=user)
        for fld in ['personal_info_status', 'document_status', 'bank_account_status']:
            if hasattr(profile, fld):
                setattr(profile, fld, 'verified')
        if hasattr(profile, 'verified_at'):
            profile.verified_at = timezone.now()
        profile.save()

    for bt, amt in [
        (Transaction.BalanceType.NAIRA_COINS, 5000),
        (Transaction.BalanceType.CRYPTO_COINS, 32),
        (Transaction.BalanceType.BONUS_COINS, 200),
        (Transaction.BalanceType.NAIRA_WITHDRAW, 8000),
        (Transaction.BalanceType.CRYPTO_WITHDRAW, 50),
    ]:
        WalletService.credit(
            user=user, amount=Decimal(str(amt)),
            balance_type=bt,
            tx_type=Transaction.Type.DEPOSIT,
            reference_id=f'{SMOKE_PREFIX}seed-{bt}-{uuid.uuid4().hex[:6]}',
        )

    BankAccount.objects.create(
        user=user, bank_code='058', bank_name='GTBank',
        account_number='0123456789', account_name='V3C6 SMOKE',
        is_default=True, is_active=True, verified_at=timezone.now(),
    )

    CryptoWallet.objects.create(
        user=user, network='TRC20', address=VALID_TRC20,
        label='Binance hot', is_default=True, is_active=True,
        verified_at=timezone.now(),
    )

    return user


def make_admin_user():
    user = User.objects.create(
        telegram_id=ADMIN_TID,
        first_name='Admin',
        last_name='V3C6',
        username='admin_v3c6',
        is_staff=True,
        is_active=True,
    )
    try:
        AdminProfile.objects.create(
            user=user,
            email='admin_v3c6@dev.local',
            role=AdminProfile.Role.SUPER_ADMIN,
            is_active=True,
        )
    except Exception:
        pass
    return user


# ═════════════════════════════════════════════════════════════════════════
# TESTS
# ═════════════════════════════════════════════════════════════════════════

def test_user_detail_v3_shape():
    section('1. AdminUserDetailView returns v3 wallet shape')

    user = make_user_with_balances()
    admin = make_admin_user()

    from apps.admin_panel.views import AdminUserDetailView
    request = build_request(f'/api/v1/admin/users/{user.id}/', admin)

    try:
        response = AdminUserDetailView().get(request, user_id=user.id)
        data = response.data.get('data', {})
    except Exception as e:
        check(False, f'View crashed: {type(e).__name__}: {e}')
        return

    check(response.status_code == 200,
          f'Status 200 (got {response.status_code})')

    wallet = data.get('wallet', {})

    for key in ['crypto_coins', 'naira_coins', 'bonus_coins',
                'crypto_withdraw_balance', 'naira_withdraw_balance', 'staked']:
        check(key in wallet, f'wallet.{key} present')

    for old_key in ['deposit_coins', 'total_coins', 'earnings',
                    'earnings_usd_equivalent']:
        check(old_key not in wallet, f'wallet.{old_key} removed')


def test_user_detail_bank_and_crypto_wallets():
    section('2. AdminUserDetailView includes bank_accounts + crypto_wallets')

    user = User.objects.get(telegram_id=SMOKE_TID)
    admin = User.objects.get(telegram_id=ADMIN_TID)

    from apps.admin_panel.views import AdminUserDetailView
    request = build_request(f'/api/v1/admin/users/{user.id}/', admin)

    response = AdminUserDetailView().get(request, user_id=user.id)
    data = response.data.get('data', {})

    bank_accounts = data.get('bank_accounts', [])
    crypto_wallets = data.get('crypto_wallets', [])

    check(isinstance(bank_accounts, list),
          f'bank_accounts is list (got {type(bank_accounts).__name__})')
    check(len(bank_accounts) == 1, f'1 bank account (got {len(bank_accounts)})')
    if bank_accounts:
        check(bank_accounts[0].get('bank_name') == 'GTBank',
              f"bank_accounts[0].bank_name = GTBank")

    check(isinstance(crypto_wallets, list),
          f'crypto_wallets is list (got {type(crypto_wallets).__name__})')
    check(len(crypto_wallets) == 1, f'1 crypto wallet (got {len(crypto_wallets)})')
    if crypto_wallets:
        check(crypto_wallets[0].get('network') == 'TRC20',
              'crypto_wallets[0].network = TRC20')

    check(data.get('bank_account') is not None,
          'bank_account (singular) present for backward compat')


def test_withdrawals_list_rail_and_currency():
    section('3. AdminWithdrawalsListView returns rail + currency per row')

    user = User.objects.get(telegram_id=SMOKE_TID)
    admin = User.objects.get(telegram_id=ADMIN_TID)

    bank = BankAccount.objects.filter(user=user, is_active=True).first()

    bank_w = WithdrawalService.request(
        user=user, amount=Decimal('1500'),
        rail='bank', bank_account=bank,
    )

    set_setting(SettingKey.CRYPTO_WITHDRAWAL_ENABLED, Decimal('1'))
    invalidate_cache()

    crypto_w = WithdrawalService.request(
        user=user, amount=Decimal('10'),
        rail='crypto', wallet_address=VALID_TRC20, network='TRC20',
    )

    from apps.admin_panel.views import AdminWithdrawalsListView
    request = build_request('/api/v1/admin/withdrawals/', admin)

    try:
        response = AdminWithdrawalsListView().get(request)
    except Exception as e:
        check(False, f'View crashed: {type(e).__name__}: {e}')
        return

    data = response.data.get('data', {})
    items = data.get('results') or data.get('items') or []

    bank_row = next((i for i in items if i.get('id') == str(bank_w.id)), None)
    crypto_row = next((i for i in items if i.get('id') == str(crypto_w.id)), None)

    check(bank_row is not None, 'Bank withdrawal found in list')
    check(crypto_row is not None, 'Crypto withdrawal found in list')

    if bank_row:
        check(bank_row.get('rail') == 'bank',
              f"bank row rail=bank (got {bank_row.get('rail')!r})")
        check(bank_row.get('currency') == 'NGN',
              f"bank row currency=NGN (got {bank_row.get('currency')!r})")

    if crypto_row:
        check(crypto_row.get('rail') == 'crypto',
              f"crypto row rail=crypto (got {crypto_row.get('rail')!r})")
        check(crypto_row.get('currency') == 'USDT',
              f"crypto row currency=USDT (got {crypto_row.get('currency')!r})")
        check(crypto_row.get('wallet_address') == VALID_TRC20,
              'crypto row wallet_address present')
        check(crypto_row.get('network') == 'TRC20',
              'crypto row network=TRC20')


def test_withdrawals_overview_per_currency():
    section('4. Overview totals split by currency')

    admin = User.objects.get(telegram_id=ADMIN_TID)

    from apps.admin_panel.views import AdminWithdrawalsListView
    request = build_request('/api/v1/admin/withdrawals/', admin)

    response = AdminWithdrawalsListView().get(request)
    overview = response.data.get('data', {}).get('overview', {})

    check('ngn' in overview, 'overview.ngn present')
    check('usdt' in overview, 'overview.usdt present')

    if 'ngn' in overview:
        check('total_pending' in overview['ngn'],
              'overview.ngn.total_pending present')
        check('total_paid' in overview['ngn'],
              'overview.ngn.total_paid present')

    if 'usdt' in overview:
        check('total_pending' in overview['usdt'],
              'overview.usdt.total_pending present')


def test_withdrawals_rail_filter():
    section('5. Filter ?rail=crypto returns only crypto rows')

    admin = User.objects.get(telegram_id=ADMIN_TID)

    from apps.admin_panel.views import AdminWithdrawalsListView
    request = build_request('/api/v1/admin/withdrawals/?rail=crypto', admin)

    response = AdminWithdrawalsListView().get(request)
    items = (
        response.data.get('data', {}).get('results')
        or response.data.get('data', {}).get('items')
        or []
    )

    if items:
        all_crypto = all(item.get('rail') == 'crypto' for item in items)
        check(all_crypto,
              f'All filtered rows have rail=crypto ({len(items)} rows)')
    else:
        check(False, 'No rows returned for ?rail=crypto (expected ≥1)')


def test_settings_v3_keys():
    section('6. Settings list returns v3 keys, no COINS_PER_*')

    from apps.settings_app.services import get_all_settings
    settings_data = get_all_settings()

    if isinstance(settings_data, list):
        keys = [s.get('key') if isinstance(s, dict) else s for s in settings_data]
    elif isinstance(settings_data, dict):
        keys = list(settings_data.keys())
    else:
        check(False, f'Unexpected shape: {type(settings_data).__name__}')
        return

    expected_v3 = {
        'BONUS_PAYOUT_RATE', 'BONUS_TO_NGN_RATE', 'BONUS_TO_USDT_RATE',
        'CRYPTO_WITHDRAWAL_ENABLED',
        'MIN_DEPOSIT_NGN', 'MIN_DEPOSIT_USD',
        'MIN_WITHDRAWAL_NGN', 'MIN_WITHDRAWAL_USDT',
        'NGN_PER_USD_DISPLAY_RATE',
    }
    found = expected_v3.intersection(keys)
    missing = expected_v3 - found

    check(len(missing) == 0,
          f'All v3 keys present (missing: {missing or "none"})')

    old_keys = ['COINS_PER_NGN', 'COINS_PER_USD', 'BONUS_WALLET_PAYOUT_RATE']
    found_old = [k for k in old_keys if k in keys]
    check(len(found_old) == 0,
          f'Old v2 keys removed (still present: {found_old or "none"})')


def test_settings_permissions_matrix():
    section('7. ROLE_PERMISSIONS has edit_settings + view_settings')

    super_perms = ROLE_PERMISSIONS.get(AdminProfile.Role.SUPER_ADMIN, set())
    finance_perms = ROLE_PERMISSIONS.get(AdminProfile.Role.FINANCE_ADMIN, set())
    risk_perms = ROLE_PERMISSIONS.get(AdminProfile.Role.RISK_ADMIN, set())
    support_perms = ROLE_PERMISSIONS.get(AdminProfile.Role.SUPPORT_ADMIN, set())
    read_only_perms = ROLE_PERMISSIONS.get(AdminProfile.Role.READ_ONLY, set())

    check('edit_settings' in super_perms, 'super_admin has edit_settings')
    check('edit_settings' in finance_perms, 'finance_admin has edit_settings')
    check('edit_settings' not in risk_perms,
          'risk_admin does NOT have edit_settings')

    check('view_settings' in super_perms, 'super_admin has view_settings')
    check('view_settings' in risk_perms, 'risk_admin has view_settings')
    check('view_settings' in support_perms, 'support_admin has view_settings')
    check('view_settings' in read_only_perms, 'read_only has view_settings')


def test_settings_can_be_updated():
    section('8. Set/get a setting works end-to-end')

    original = get_setting(SettingKey.BONUS_PAYOUT_RATE)
    try:
        set_setting(SettingKey.BONUS_PAYOUT_RATE, Decimal('0.5'))
        invalidate_cache()
        new_val = get_setting(SettingKey.BONUS_PAYOUT_RATE)
        check(new_val == Decimal('0.5'),
              f'BONUS_PAYOUT_RATE set to 0.5 (got {new_val})')
    finally:
        set_setting(SettingKey.BONUS_PAYOUT_RATE, original)
        invalidate_cache()


# ═════════════════════════════════════════════════════════════════════════
# RUNNER
# ═════════════════════════════════════════════════════════════════════════

def main():
    print('=' * 70)
    print('  SPIN REWARDS v3 — CHUNK 6 SMOKE TEST (ADMIN ENDPOINTS)')
    print('=' * 70)

    cleanup()
    set_setting(SettingKey.CRYPTO_WITHDRAWAL_ENABLED, Decimal('0'))
    invalidate_cache()

    try:
        test_user_detail_v3_shape()
        test_user_detail_bank_and_crypto_wallets()
        test_withdrawals_list_rail_and_currency()
        test_withdrawals_overview_per_currency()
        test_withdrawals_rail_filter()
        test_settings_v3_keys()
        test_settings_permissions_matrix()
        test_settings_can_be_updated()
    finally:
        cleanup()
        set_setting(SettingKey.CRYPTO_WITHDRAWAL_ENABLED, Decimal('0'))
        invalidate_cache()

    print(f'\n{"=" * 70}')
    print(f'  RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print('=' * 70)

    if TESTS_FAILED:
        print('\nFAILED:')
        for label in FAIL_DETAILS:
            print(f'  ✗ {label}')
        print('\n❌ CHUNK 6 FAILED — fix before proceeding to Chunk 7')
        sys.exit(1)
    else:
        print('\n✅ CHUNK 6 GREEN — ready for Chunk 7')
        sys.exit(0)


if __name__ == '__main__':
    main()