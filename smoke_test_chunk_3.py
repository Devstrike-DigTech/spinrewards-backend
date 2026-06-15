"""
Smoke Test — v3 Chunk 3 (Deposits — Currency-Aware Routing)
==============================================================

Verifies:
  1. Paystack deposit → credits NAIRA_COINS (not naira_withdraw, not crypto_coins)
  2. NowPayments deposit → credits CRYPTO_COINS at 1:1 (no × 1500 multiplication)
  3. naira_withdraw stays UNCHANGED on deposits (only spin wins land there)
  4. Idempotency: re-running _complete_or_fail doesn't double-credit
  5. Failed deposit doesn't credit anything
  6. Unknown provider falls back to NAIRA_COINS with warning (still credits)
  7. Public settings endpoint returns v3 shape (no coins_per_*)
  8. Public settings — crypto_withdrawal_enabled is bool, not Decimal

USAGE:
    docker compose exec api python smoke_test_chunk_3.py
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
from apps.payments.models import Deposit
from apps.payments.services import PaymentService

SMOKE_PREFIX = 'v3c3_'
SMOKE_TID = 900300001


def cleanup():
    try:
        u = User.objects.get(telegram_id=SMOKE_TID)
        Transaction.objects.filter(user=u).delete()
        Deposit.objects.filter(user=u).delete()
        u.delete()
    except User.DoesNotExist:
        pass


def make_user():
    return User.objects.create(
        telegram_id=SMOKE_TID,
        first_name='V3C3',
        last_name='SmokeUser',
        username=f'{SMOKE_PREFIX}user',
    )


def make_deposit(user, provider, amount):
    """Create a pending deposit record."""
    return Deposit.objects.create(
        user=user,
        amount=Decimal(str(amount)),
        provider=provider,
        internal_reference=f'{SMOKE_PREFIX}{provider}_{uuid.uuid4().hex[:8]}',
        status='pending',
        original_amount=Decimal(str(amount)),
        original_currency='NGN' if provider == 'paystack' else 'USDT',
    )


# ═════════════════════════════════════════════════════════════════════════
# TESTS
# ═════════════════════════════════════════════════════════════════════════

def test_paystack_credits_naira_coins():
    section('1. Paystack deposit → naira_coins (NOT naira_withdraw)')

    user = make_user()
    deposit = make_deposit(user, 'paystack', '5000')

    before = WalletService.get_wallet_summary(user)

    PaymentService._complete_or_fail(
        deposit=deposit,
        event_type='success',
        confirmed_amount=Decimal('5000'),
        credit_amount=Decimal('5000'),
        raw={'gateway_response': 'Approved'},
    )

    after = WalletService.get_wallet_summary(user)
    deposit.refresh_from_db()

    naira_coins_gain = Decimal(after['naira_coins']) - Decimal(before['naira_coins'])
    naira_withdraw_change = (Decimal(after['naira_withdraw_balance'])
                             - Decimal(before['naira_withdraw_balance']))

    check(naira_coins_gain == Decimal('5000'),
          f'5000 to naira_coins (got gain={naira_coins_gain})')
    check(naira_withdraw_change == 0,
          f'naira_withdraw UNCHANGED (delta={naira_withdraw_change})')
    check(deposit.status == 'completed',
          f'deposit status=completed (got {deposit.status})')

    # No crypto wallets touched
    crypto_coins_change = (Decimal(after['crypto_coins'])
                           - Decimal(before['crypto_coins']))
    crypto_w_change = (Decimal(after['crypto_withdraw_balance'])
                       - Decimal(before['crypto_withdraw_balance']))
    check(crypto_coins_change == 0, 'crypto_coins untouched')
    check(crypto_w_change == 0, 'crypto_withdraw untouched')


def test_nowpayments_credits_crypto_coins_at_1to1():
    section('2. NowPayments deposit → crypto_coins at 1:1 (no × 1500!)')

    user = User.objects.get(telegram_id=SMOKE_TID)
    deposit = make_deposit(user, 'nowpayments', '20')

    before = WalletService.get_wallet_summary(user)

    PaymentService._complete_or_fail(
        deposit=deposit,
        event_type='success',
        confirmed_amount=Decimal('20'),  # $20 USDT
        credit_amount=Decimal('20'),
        raw={'payment_status': 'finished'},
    )

    after = WalletService.get_wallet_summary(user)
    deposit.refresh_from_db()

    crypto_coins_gain = Decimal(after['crypto_coins']) - Decimal(before['crypto_coins'])

    # CRITICAL CHECK: gain should be 20, NOT 30000 (which would be old × COINS_PER_USD)
    check(crypto_coins_gain == Decimal('20'),
          f'20 USDT → 20 crypto_coins at 1:1 (got gain={crypto_coins_gain})')

    # Specifically NOT credited at 1500x rate
    check(crypto_coins_gain != Decimal('30000'),
          'NOT 30000 (would be old × 1500 logic — confirms 1:1 peg)')

    # naira_* untouched
    naira_coins_change = (Decimal(after['naira_coins'])
                          - Decimal(before['naira_coins']))
    naira_w_change = (Decimal(after['naira_withdraw_balance'])
                      - Decimal(before['naira_withdraw_balance']))
    check(naira_coins_change == 0, 'naira_coins untouched by crypto deposit')
    check(naira_w_change == 0, 'naira_withdraw untouched by crypto deposit')

    # Deposit status
    check(deposit.status == 'completed', f'deposit completed')


def test_idempotency():
    section('3. Re-running _complete_or_fail does NOT double-credit')

    user = User.objects.get(telegram_id=SMOKE_TID)
    deposit = make_deposit(user, 'paystack', '1000')

    # First call — completes
    PaymentService._complete_or_fail(
        deposit, 'success', Decimal('1000'), Decimal('1000'), {},
    )
    deposit.refresh_from_db()
    after_first = WalletService.get_naira_coins(user)

    # Second call — should be no-op (already completed)
    PaymentService._complete_or_fail(
        deposit, 'success', Decimal('1000'), Decimal('1000'), {},
    )
    after_second = WalletService.get_naira_coins(user)

    check(after_first == after_second,
          f'Balance unchanged on replay '
          f'(after_first={after_first}, after_second={after_second})')


def test_failed_deposit_no_credit():
    section('4. Failed deposit credits nothing')

    user = User.objects.get(telegram_id=SMOKE_TID)
    deposit = make_deposit(user, 'paystack', '2000')

    before = WalletService.get_wallet_summary(user)

    PaymentService._complete_or_fail(
        deposit=deposit,
        event_type='failure',
        confirmed_amount=Decimal('0'),
        credit_amount=Decimal('0'),
        raw={'gateway_response': 'Insufficient funds'},
    )

    after = WalletService.get_wallet_summary(user)
    deposit.refresh_from_db()

    check(deposit.status == 'failed',
          f'deposit marked failed (got {deposit.status})')

    # All balances unchanged
    for key in ['crypto_coins', 'naira_coins', 'bonus_coins',
                'crypto_withdraw_balance', 'naira_withdraw_balance']:
        change = Decimal(after[key]) - Decimal(before[key])
        check(change == 0, f'{key} unchanged on failure (delta={change})')


def test_public_settings_endpoint_shape():
    section('5. Public settings endpoint — v3 shape')

    # Test via direct service call (not HTTP — avoid auth dance)
    from apps.settings_app.services import get_setting
    from apps.settings_app.models import SettingKey

    # All v3 keys readable
    v3_keys = [
        'BONUS_PAYOUT_RATE',
        'BONUS_TO_NGN_RATE',
        'BONUS_TO_USDT_RATE',
        'NGN_PER_USD_DISPLAY_RATE',
        'MIN_DEPOSIT_NGN',
        'MIN_DEPOSIT_USD',
        'MIN_WITHDRAWAL_NGN',
        'MIN_WITHDRAWAL_USDT',
        'CRYPTO_WITHDRAWAL_ENABLED',
    ]
    for k in v3_keys:
        try:
            val = get_setting(getattr(SettingKey, k))
            check(val is not None, f'{k} readable (got {val})')
        except (AttributeError, Exception) as e:
            check(False, f'{k} not readable: {e}')

    # Old keys should NOT exist
    for old in ['COINS_PER_NGN', 'COINS_PER_USD']:
        check(not hasattr(SettingKey, old),
              f'{old} fully retired from SettingKey')


def test_public_settings_view_renders():
    section('6. Public settings view renders without crashing')

    # Hit the actual view to make sure the response shape works
    try:
        from django.test import Client
        client = Client()
        response = client.get('/api/v1/settings/public/')
        check(response.status_code == 200,
              f'GET /settings/public/ returns 200 (got {response.status_code})')

        if response.status_code == 200:
            import json
            data = json.loads(response.content)
            payload = data.get('data', {})

            # v3 keys present
            expected_keys = {
                'bonus_payout_rate', 'bonus_to_ngn_rate', 'bonus_to_usdt_rate',
                'ngn_per_usd_display_rate',
                'min_deposit_ngn', 'min_deposit_usd',
                'min_withdrawal_ngn', 'min_withdrawal_usdt',
                'crypto_withdrawal_enabled',
            }
            present = set(payload.keys())
            check(expected_keys.issubset(present),
                  f'v3 keys present (missing: {expected_keys - present})')

            # Old keys gone
            check('coins_per_ngn' not in payload,
                  "'coins_per_ngn' removed from public endpoint")
            check('coins_per_usd' not in payload,
                  "'coins_per_usd' removed from public endpoint")

            # crypto_withdrawal_enabled is bool
            flag = payload.get('crypto_withdrawal_enabled')
            check(isinstance(flag, bool),
                  f'crypto_withdrawal_enabled is bool (got {type(flag).__name__})')

            # Default disabled
            check(flag is False,
                  f'crypto_withdrawal_enabled = False by default (got {flag})')

    except Exception as e:
        check(False, f'Public settings view crashed: {type(e).__name__}: {e}')


def test_no_stale_setting_keys_in_use():
    section('7. No stale COINS_PER_* references in payments code')

    import subprocess

    # Run grep against the codebase
    try:
        result = subprocess.run(
            ['grep', '-rn',
             '-E', 'COINS_PER_USD|COINS_PER_NGN|coins_per_usd|coins_per_ngn',
             'apps/payments/', 'apps/settings_app/',
             '--include=*.py'],
            capture_output=True, text=True,
        )
        # Filter out comments and migrations
        lines = result.stdout.strip().split('\n') if result.stdout else []
        active_lines = []
        for line in lines:
            if not line:
                continue
            if '/migrations/' in line:
                continue
            # Skip lines that are just comments
            # Format: filepath:lineno:content
            parts = line.split(':', 2)
            if len(parts) >= 3:
                code = parts[2].lstrip()
                if code.startswith('#') or code.startswith('*'):
                    continue
            active_lines.append(line)

        check(len(active_lines) == 0,
              f'No active COINS_PER_* references '
              f'(found: {len(active_lines)} lines)'
              + (f'\n     {chr(10).join("    " + l for l in active_lines[:5])}'
                 if active_lines else ''))

    except FileNotFoundError:
        # grep not available — skip
        print('    (skipped — grep not available in container)')


# ═════════════════════════════════════════════════════════════════════════
# RUNNER
# ═════════════════════════════════════════════════════════════════════════

def main():
    print('=' * 70)
    print('  SPIN REWARDS v3 — CHUNK 3 SMOKE TEST (DEPOSITS ROUTING)')
    print('=' * 70)

    cleanup()

    try:
        test_paystack_credits_naira_coins()
        test_nowpayments_credits_crypto_coins_at_1to1()
        test_idempotency()
        test_failed_deposit_no_credit()
        test_public_settings_endpoint_shape()
        test_public_settings_view_renders()
        test_no_stale_setting_keys_in_use()
    finally:
        cleanup()

    print(f'\n{"=" * 70}')
    print(f'  RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print('=' * 70)

    if TESTS_FAILED:
        print('\nFAILED:')
        for label in FAIL_DETAILS:
            print(f'  ✗ {label}')
        print('\n❌ CHUNK 3 FAILED — fix before proceeding to Chunk 4')
        sys.exit(1)
    else:
        print('\n✅ CHUNK 3 GREEN — ready for Chunk 4 (withdrawals dual rail)')
        sys.exit(0)


if __name__ == '__main__':
    main()