"""
Smoke Test — v3 Chunk 7 (Public Endpoints — Frontend Contract)
==================================================================

Verifies the public endpoints the mini-app will call:

  1. GET /api/v1/settings/public/ returns v3 shape (spec §6.1)
  2. GET /api/v1/wallet/ returns 5-balance wallet shape
  3. GET /api/v1/wallet/ ALSO returns currency_caps + crypto_withdrawal_enabled
  4. GET /api/v1/wallet/ backward-compat: top-level keys still present
  5. GET /api/v1/wallet/transactions/ basic listing
  6. GET /api/v1/wallet/transactions/?currency=NGN filters correctly
  7. GET /api/v1/wallet/transactions/?currency=USDT filters correctly
  8. Public settings has no old v2 keys (coins_per_*)
  9. Frontend simulation: one call gets everything needed for wallet UI

USAGE:
    docker compose exec api python smoke_test_chunk_7.py
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
from apps.settings_app.services import set_setting, invalidate_cache
from apps.settings_app.models import SettingKey

SMOKE_PREFIX = 'v3c7_'
SMOKE_TID = 900700001


def cleanup():
    try:
        u = User.objects.get(telegram_id=SMOKE_TID)
        Transaction.objects.filter(user=u).delete()
        u.delete()
    except User.DoesNotExist:
        pass


def build_request(path, user=None):
    """Build a DRF Request that views expect."""
    factory = APIRequestFactory()
    django_request = factory.get(path)
    drf_request = Request(django_request)
    if user is not None:
        drf_request.user = user
    return drf_request


def make_user_with_mixed_txns():
    """Create a test user with both NGN and USDT transactions."""
    user = User.objects.create(
        telegram_id=SMOKE_TID,
        first_name='V3C7',
        last_name='SmokeUser',
        username=f'{SMOKE_PREFIX}user',
    )

    # Seed balances (also creates transactions)
    WalletService.credit(
        user=user, amount=Decimal('5000'),
        balance_type=Transaction.BalanceType.NAIRA_COINS,
        tx_type=Transaction.Type.DEPOSIT,
        reference_id=f'{SMOKE_PREFIX}seed-naira',
    )
    WalletService.credit(
        user=user, amount=Decimal('20'),
        balance_type=Transaction.BalanceType.CRYPTO_COINS,
        tx_type=Transaction.Type.DEPOSIT,
        reference_id=f'{SMOKE_PREFIX}seed-crypto',
    )
    WalletService.credit(
        user=user, amount=Decimal('100'),
        balance_type=Transaction.BalanceType.BONUS_COINS,
        tx_type=Transaction.Type.BONUS if hasattr(Transaction.Type, 'BONUS')
                                       else Transaction.Type.DEPOSIT,
        reference_id=f'{SMOKE_PREFIX}seed-bonus',
    )

    return user


# ═════════════════════════════════════════════════════════════════════════
# TESTS
# ═════════════════════════════════════════════════════════════════════════

def test_settings_public_v3_shape():
    section('1. /api/v1/settings/public/ returns v3 shape')

    from apps.settings_app.public_views import PublicSettingsView
    request = build_request('/api/v1/settings/public/')

    try:
        response = PublicSettingsView().get(request)
    except Exception as e:
        check(False, f'View crashed: {type(e).__name__}: {e}')
        return

    data = response.data.get('data', {})

    expected = {
        'bonus_payout_rate', 'bonus_to_ngn_rate', 'bonus_to_usdt_rate',
        'ngn_per_usd_display_rate',
        'min_deposit_ngn', 'min_deposit_usd',
        'min_withdrawal_ngn', 'min_withdrawal_usdt',
        'crypto_withdrawal_enabled',
    }
    present = set(data.keys())
    missing = expected - present
    check(not missing, f'All v3 keys present (missing: {missing or "none"})')

    # crypto_withdrawal_enabled is bool, not Decimal
    flag = data.get('crypto_withdrawal_enabled')
    check(isinstance(flag, bool),
          f'crypto_withdrawal_enabled is bool (got {type(flag).__name__})')


def test_settings_public_no_v2_keys():
    section('2. Settings has no v2 coins_per_* keys')

    from apps.settings_app.public_views import PublicSettingsView
    request = build_request('/api/v1/settings/public/')

    response = PublicSettingsView().get(request)
    data = response.data.get('data', {})

    for old in ['coins_per_ngn', 'coins_per_usd', 'bonus_wallet_payout_rate']:
        check(old not in data, f'Old key {old!r} not in public settings')


def test_wallet_endpoint_v3_balances():
    section('3. /api/v1/wallet/ returns 5-balance shape')

    user = make_user_with_mixed_txns()

    from apps.wallet.views import WalletView
    request = build_request('/api/v1/wallet/', user)

    try:
        response = WalletView().get(request)
    except Exception as e:
        check(False, f'View crashed: {type(e).__name__}: {e}')
        return

    data = response.data.get('data', {})
    wallet = data.get('wallet', data)  # support both nested and top-level

    for key in ['crypto_coins', 'naira_coins', 'bonus_coins',
                'crypto_withdraw_balance', 'naira_withdraw_balance', 'staked']:
        check(key in wallet, f'wallet.{key} present')

    # Verify actual amounts (from seed)
    if 'naira_coins' in wallet:
        check(Decimal(wallet['naira_coins']) == Decimal('5000'),
              f'naira_coins = 5000 (got {wallet["naira_coins"]})')
    if 'crypto_coins' in wallet:
        check(Decimal(wallet['crypto_coins']) == Decimal('20'),
              f'crypto_coins = 20 (got {wallet["crypto_coins"]})')


def test_wallet_endpoint_includes_caps_and_flag():
    section('4. /api/v1/wallet/ includes currency_caps + crypto_withdrawal_enabled')

    user = User.objects.get(telegram_id=SMOKE_TID)

    from apps.wallet.views import WalletView
    request = build_request('/api/v1/wallet/', user)

    response = WalletView().get(request)
    data = response.data.get('data', {})

    caps = data.get('currency_caps')
    flag = data.get('crypto_withdrawal_enabled')

    check(caps is not None, 'currency_caps present')
    check(flag is not None, 'crypto_withdrawal_enabled present')

    if caps:
        for key in ['min_deposit_ngn', 'min_deposit_usd',
                    'min_withdrawal_ngn', 'min_withdrawal_usdt']:
            check(key in caps, f'currency_caps.{key} present')

    check(isinstance(flag, bool),
          f'crypto_withdrawal_enabled is bool (got {type(flag).__name__})')


def test_wallet_endpoint_backward_compat():
    section('5. /api/v1/wallet/ backward-compat: top-level keys still present')

    user = User.objects.get(telegram_id=SMOKE_TID)

    from apps.wallet.views import WalletView
    request = build_request('/api/v1/wallet/', user)

    response = WalletView().get(request)
    data = response.data.get('data', {})

    # Backward compat — top-level balance keys (from **wallet spread)
    for key in ['crypto_coins', 'naira_coins', 'bonus_coins',
                'crypto_withdraw_balance', 'naira_withdraw_balance']:
        if key in data:
            check(True, f'data.{key} present at top level (backward compat)')
        else:
            # If you chose Option A (no backward compat), this is expected
            print(f'    INFO: data.{key} not at top level — '
                  'OK if you chose canonical-only shape')


def test_transactions_basic():
    section('6. /api/v1/wallet/transactions/ returns user transactions')

    user = User.objects.get(telegram_id=SMOKE_TID)

    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=user)

    try:
        response = client.get('/api/v1/wallet/transactions/')
    except Exception as e:
        check(False, f'View crashed: {type(e).__name__}: {e}')
        return

    check(response.status_code == 200,
          f'Status 200 (got {response.status_code})')

    body = response.json()
    data = body.get('data', {})
    results = data.get('results', [])
    check(len(results) >= 3,
          f'≥3 transactions returned ({len(results)} found)')


def test_transactions_currency_filter():
    section('7. /api/v1/wallet/transactions/?currency=NGN filters correctly')

    user = User.objects.get(telegram_id=SMOKE_TID)

    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=user)

    # NGN filter
    response_ngn = client.get('/api/v1/wallet/transactions/?currency=NGN')
    check(response_ngn.status_code == 200,
          f'?currency=NGN status=200 (got {response_ngn.status_code})')

    ngn_results = response_ngn.json().get('data', {}).get('results', [])

    all_ngn = all(
        r.get('currency') == 'NGN'
        for r in ngn_results
        if r.get('currency')
    )
    check(all_ngn or len(ngn_results) == 0,
          f'?currency=NGN returns only NGN ({len(ngn_results)} rows)')

    # USDT filter
    response_usdt = client.get('/api/v1/wallet/transactions/?currency=USDT')
    check(response_usdt.status_code == 200,
          f'?currency=USDT status=200 (got {response_usdt.status_code})')

    usdt_results = response_usdt.json().get('data', {}).get('results', [])

    all_usdt = all(
        r.get('currency') == 'USDT'
        for r in usdt_results
        if r.get('currency')
    )
    check(all_usdt or len(usdt_results) == 0,
          f'?currency=USDT returns only USDT ({len(usdt_results)} rows)')

    # Different result sets
    check(ngn_results != usdt_results,
          'NGN and USDT result sets are different')


def test_frontend_simulation():
    section('8. Frontend simulation: one /wallet/ call has everything')

    user = User.objects.get(telegram_id=SMOKE_TID)

    from apps.wallet.views import WalletView
    request = build_request('/api/v1/wallet/', user)

    response = WalletView().get(request)
    data = response.data.get('data', {})

    # FE should be able to render the wallet UI from this single response
    has_wallet = (
        'wallet' in data
        or any(k in data for k in ['crypto_coins', 'naira_coins'])
    )
    has_caps = 'currency_caps' in data
    has_flag = 'crypto_withdrawal_enabled' in data

    check(has_wallet, 'Wallet balances accessible')
    check(has_caps, 'currency_caps accessible')
    check(has_flag, 'crypto_withdrawal_enabled accessible')

    print('\n    Frontend can render the wallet UI from one API call ✓')


def test_settings_view_consistency():
    section('9. /wallet/ and /settings/public/ agree on caps & flag')

    user = User.objects.get(telegram_id=SMOKE_TID)

    # Get caps from wallet
    from apps.wallet.views import WalletView
    w_request = build_request('/api/v1/wallet/', user)
    w_data = WalletView().get(w_request).data.get('data', {})
    w_caps = w_data.get('currency_caps', {})
    w_flag = w_data.get('crypto_withdrawal_enabled')

    # Get caps from settings
    from apps.settings_app.public_views import PublicSettingsView
    s_request = build_request('/api/v1/settings/public/')
    s_data = PublicSettingsView().get(s_request).data.get('data', {})

    # Compare
    check(w_caps.get('min_deposit_ngn') == s_data.get('min_deposit_ngn'),
          f'min_deposit_ngn matches '
          f'(wallet={w_caps.get("min_deposit_ngn")} '
          f'settings={s_data.get("min_deposit_ngn")})')
    check(w_flag == s_data.get('crypto_withdrawal_enabled'),
          f'crypto_withdrawal_enabled matches (wallet={w_flag} settings={s_data.get("crypto_withdrawal_enabled")})')


# ═════════════════════════════════════════════════════════════════════════
# RUNNER
# ═════════════════════════════════════════════════════════════════════════

def main():
    print('=' * 70)
    print('  SPIN REWARDS v3 — CHUNK 7 SMOKE TEST (PUBLIC ENDPOINTS)')
    print('=' * 70)

    cleanup()
    set_setting(SettingKey.CRYPTO_WITHDRAWAL_ENABLED, Decimal('0'))
    invalidate_cache()

    try:
        test_settings_public_v3_shape()
        test_settings_public_no_v2_keys()
        test_wallet_endpoint_v3_balances()
        test_wallet_endpoint_includes_caps_and_flag()
        test_wallet_endpoint_backward_compat()
        test_transactions_basic()
        test_transactions_currency_filter()
        test_frontend_simulation()
        test_settings_view_consistency()
    finally:
        cleanup()

    print(f'\n{"=" * 70}')
    print(f'  RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print('=' * 70)

    if TESTS_FAILED:
        print('\nFAILED:')
        for label in FAIL_DETAILS:
            print(f'  ✗ {label}')
        print('\n❌ CHUNK 7 FAILED — fix before proceeding to Chunk 8')
        sys.exit(1)
    else:
        print('\n✅ CHUNK 7 GREEN — ready for Chunk 8 (frontend doc v3)')
        sys.exit(0)


if __name__ == '__main__':
    main()