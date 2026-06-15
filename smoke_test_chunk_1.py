"""
Smoke Test — v3 Chunk 1 (Wallet Foundation)
===========================================

Verifies:
  1. New BalanceType enum has 6 values (5 spendable + STAKED)
  2. Transaction has a currency field (NGN | USDT)
  3. WalletService.get_wallet_summary returns 5 balances + USD equiv
  4. All 5 spendable balance types accept credits
  5. credit/debit/lock whitelists are updated
  6. _resolve_currency auto-tags new transactions correctly
  7. New SettingKey has BONUS_TO_NGN_RATE, BONUS_TO_USDT_RATE, etc.
  8. CRYPTO_WITHDRAWAL_ENABLED flag is readable

USAGE:
    docker compose exec api python smoke_test_chunk_1.py
"""
import os
import sys
import uuid
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

# ─── Test runner ─────────────────────────────────────────────────────────
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


# ─── Imports & fixtures ──────────────────────────────────────────────────
from apps.users.models import User
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService

SMOKE_TID = 900100001


def cleanup():
    try:
        u = User.objects.get(telegram_id=SMOKE_TID)
        Transaction.objects.filter(user=u).delete()
        u.delete()
    except User.DoesNotExist:
        pass


def make_user():
    return User.objects.create(
        telegram_id=SMOKE_TID,
        first_name='V3SmokeTest',
        last_name='User',
        username=f'v3_smoke_{SMOKE_TID}',
    )


def credit_helper(user, balance_type, amount, currency=None):
    """Use the proper service method to credit."""
    return WalletService.credit(
        user=user,
        amount=Decimal(str(amount)),
        balance_type=balance_type,
        tx_type=Transaction.Type.DEPOSIT,
        reference_id=f'v3-smoke-{uuid.uuid4().hex[:8]}',
    )


# ═════════════════════════════════════════════════════════════════════════
# TESTS
# ═════════════════════════════════════════════════════════════════════════

def test_balance_type_enum():
    section('1. BalanceType enum — v3 values')

    BT = Transaction.BalanceType
    expected = {
        'crypto_coins', 'naira_coins', 'bonus_coins',
        'crypto_withdraw', 'naira_withdraw', 'staked',
    }
    actual = {value for value, _ in BT.choices}

    check(expected == actual,
          f'6 balance types defined (got {sorted(actual)})')

    # Critical: old values are GONE
    check('deposit_coins' not in actual, "'deposit_coins' removed")
    check('earnings' not in actual, "'earnings' removed")
    check('coin' not in actual, "Legacy 'coin' gone")
    check('cash' not in actual, "Legacy 'cash' gone")


def test_currency_field():
    section('2. Transaction.currency field — NGN | USDT')

    check(hasattr(Transaction, 'Currency'),
          'Transaction.Currency enum exists')

    if hasattr(Transaction, 'Currency'):
        currencies = {v for v, _ in Transaction.Currency.choices}
        check(currencies == {'NGN', 'USDT'},
              f'Currency choices = NGN/USDT (got {currencies})')

    # Verify field is on the model
    field_names = {f.name for f in Transaction._meta.get_fields()}
    check('currency' in field_names,
          'Transaction has currency field')


def test_wallet_summary_shape():
    section('3. get_wallet_summary returns 5 balances + USD equiv')

    user = make_user()
    summary = WalletService.get_wallet_summary(user)

    expected_keys = {
        'crypto_coins', 'naira_coins', 'bonus_coins',
        'crypto_withdraw_balance', 'naira_withdraw_balance',
        'naira_withdraw_usd_equivalent', 'staked',
    }
    actual_keys = set(summary.keys())

    check(actual_keys == expected_keys,
          f'Summary has expected keys (missing: {expected_keys - actual_keys}, '
          f'extra: {actual_keys - expected_keys})')

    # All start at zero for a new user
    check(summary['crypto_coins'] == '0.000000',
          f'crypto_coins starts at 0.000000 (got {summary["crypto_coins"]})')
    check(summary['naira_coins'] == '0.00',
          f'naira_coins starts at 0.00 (got {summary["naira_coins"]})')
    check(summary['bonus_coins'] == '0.00',
          f'bonus_coins starts at 0.00 (got {summary["bonus_coins"]})')


def test_credit_all_buckets():
    section('4. credit() accepts all 5 spendable balance types')

    user = User.objects.get(telegram_id=SMOKE_TID)
    BT = Transaction.BalanceType

    try:
        credit_helper(user, BT.CRYPTO_COINS, '0.5')
        check(True, 'credit() crypto_coins')
    except Exception as e:
        check(False, f'credit() crypto_coins failed: {e}')

    try:
        credit_helper(user, BT.NAIRA_COINS, '1000')
        check(True, 'credit() naira_coins')
    except Exception as e:
        check(False, f'credit() naira_coins failed: {e}')

    try:
        credit_helper(user, BT.BONUS_COINS, '500')
        check(True, 'credit() bonus_coins')
    except Exception as e:
        check(False, f'credit() bonus_coins failed: {e}')

    try:
        credit_helper(user, BT.CRYPTO_WITHDRAW, '1.5')
        check(True, 'credit() crypto_withdraw')
    except Exception as e:
        check(False, f'credit() crypto_withdraw failed: {e}')

    try:
        credit_helper(user, BT.NAIRA_WITHDRAW, '5000')
        check(True, 'credit() naira_withdraw')
    except Exception as e:
        check(False, f'credit() naira_withdraw failed: {e}')

    # Verify the summary reflects all credits
    summary = WalletService.get_wallet_summary(user)
    check(Decimal(summary['crypto_coins']) == Decimal('0.5'),
          f'crypto_coins = 0.5 (got {summary["crypto_coins"]})')
    check(Decimal(summary['naira_coins']) == Decimal('1000'),
          f'naira_coins = 1000 (got {summary["naira_coins"]})')
    check(Decimal(summary['bonus_coins']) == Decimal('500'),
          f'bonus_coins = 500 (got {summary["bonus_coins"]})')
    check(Decimal(summary['crypto_withdraw_balance']) == Decimal('1.5'),
          f'crypto_withdraw = 1.5 (got {summary["crypto_withdraw_balance"]})')
    check(Decimal(summary['naira_withdraw_balance']) == Decimal('5000'),
          f'naira_withdraw = 5000 (got {summary["naira_withdraw_balance"]})')


def test_currency_auto_tagging():
    section('5. Transactions are auto-tagged with currency')

    user = User.objects.get(telegram_id=SMOKE_TID)

    # Get the most recent crypto_coins credit and check its currency
    crypto_tx = Transaction.objects.filter(
        user=user,
        balance_type=Transaction.BalanceType.CRYPTO_COINS,
    ).first()
    check(crypto_tx is not None, 'crypto_coins transaction exists')
    if crypto_tx:
        check(crypto_tx.currency == 'USDT',
              f'crypto_coins → USDT (got {crypto_tx.currency})')

    naira_tx = Transaction.objects.filter(
        user=user,
        balance_type=Transaction.BalanceType.NAIRA_COINS,
    ).first()
    if naira_tx:
        check(naira_tx.currency == 'NGN',
              f'naira_coins → NGN (got {naira_tx.currency})')

    crypto_w_tx = Transaction.objects.filter(
        user=user,
        balance_type=Transaction.BalanceType.CRYPTO_WITHDRAW,
    ).first()
    if crypto_w_tx:
        check(crypto_w_tx.currency == 'USDT',
              f'crypto_withdraw → USDT (got {crypto_w_tx.currency})')

    naira_w_tx = Transaction.objects.filter(
        user=user,
        balance_type=Transaction.BalanceType.NAIRA_WITHDRAW,
    ).first()
    if naira_w_tx:
        check(naira_w_tx.currency == 'NGN',
              f'naira_withdraw → NGN (got {naira_w_tx.currency})')


def test_lock_whitelist():
    section('6. lock() rejects non-spendable balance types')

    user = User.objects.get(telegram_id=SMOKE_TID)
    BT = Transaction.BalanceType
    raised_count = 0

    # Cannot lock from withdraw balances
    for bad_type in (BT.CRYPTO_WITHDRAW, BT.NAIRA_WITHDRAW, BT.STAKED):
        try:
            WalletService.lock(
                user=user,
                amount=Decimal('100'),
                source_balance_type=bad_type,
                reference_id=f'v3-lock-bad-{uuid.uuid4().hex[:8]}',
            )
            check(False, f'lock({bad_type}) should have raised, did not')
        except ValueError:
            raised_count += 1
        except Exception as e:
            check(False, f'lock({bad_type}) raised wrong type: {type(e).__name__}: {e}')

    check(raised_count == 3,
          f'lock() rejected 3 non-spendable types (raised {raised_count})')


def test_settings_keys():
    section('7. New SettingKey values exist with correct defaults')

    from apps.settings_app.models import SettingKey
    from apps.settings_app.services import get_setting, invalidate_cache

    invalidate_cache()

    # New v3 keys
    new_keys = [
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

    for k in new_keys:
        check(hasattr(SettingKey, k), f'SettingKey.{k} defined')
        check(k in SettingKey.DEFAULTS, f'SettingKey.DEFAULTS has {k}')

    # Old keys retired
    retired = ['COINS_PER_NGN', 'COINS_PER_USD', 'BONUS_WALLET_PAYOUT_RATE']
    for k in retired:
        check(not hasattr(SettingKey, k) or k not in SettingKey.DEFAULTS,
              f'SettingKey.{k} retired')

    # Default values per spec
    if hasattr(SettingKey, 'BONUS_TO_NGN_RATE'):
        rate = get_setting(SettingKey.BONUS_TO_NGN_RATE)
        check(rate == Decimal('1'),
              f'BONUS_TO_NGN_RATE default = 1 (got {rate})')

    if hasattr(SettingKey, 'BONUS_TO_USDT_RATE'):
        rate = get_setting(SettingKey.BONUS_TO_USDT_RATE)
        check(rate == Decimal('1'),
              f'BONUS_TO_USDT_RATE default = 1 (got {rate})')

    if hasattr(SettingKey, 'BONUS_PAYOUT_RATE'):
        rate = get_setting(SettingKey.BONUS_PAYOUT_RATE)
        check(rate == Decimal('0.40'),
              f'BONUS_PAYOUT_RATE default = 0.40 (got {rate})')


def test_crypto_withdrawal_flag():
    section('8. CRYPTO_WITHDRAWAL_ENABLED flag is readable')

    from apps.settings_app.models import SettingKey
    from apps.settings_app.services import get_setting

    if not hasattr(SettingKey, 'CRYPTO_WITHDRAWAL_ENABLED'):
        check(False, 'CRYPTO_WITHDRAWAL_ENABLED key not defined')
        return

    flag = get_setting(SettingKey.CRYPTO_WITHDRAWAL_ENABLED)
    check(isinstance(flag, Decimal),
          f'flag returns Decimal (got {type(flag).__name__})')

    # Default is 0 (disabled)
    check(flag == Decimal('0'),
          f'CRYPTO_WITHDRAWAL_ENABLED default = 0 (disabled) (got {flag})')

    # Sanity check the truthiness pattern
    is_enabled = flag > 0
    check(is_enabled is False,
          f'flag > 0 evaluates to False when disabled')


# ═════════════════════════════════════════════════════════════════════════
# RUNNER
# ═════════════════════════════════════════════════════════════════════════

def main():
    print('=' * 70)
    print('  SPIN REWARDS v3 — CHUNK 1 SMOKE TEST (WALLET FOUNDATION)')
    print('=' * 70)

    cleanup()

    try:
        test_balance_type_enum()
        test_currency_field()
        test_wallet_summary_shape()
        test_credit_all_buckets()
        test_currency_auto_tagging()
        test_lock_whitelist()
        test_settings_keys()
        test_crypto_withdrawal_flag()
    finally:
        cleanup()

    print(f'\n{"=" * 70}')
    print(f'  RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print('=' * 70)

    if TESTS_FAILED:
        print('\nFAILED:')
        for label in FAIL_DETAILS:
            print(f'  ✗ {label}')
        print('\n❌ CHUNK 1 FAILED — fix before proceeding to Chunk 2')
        sys.exit(1)
    else:
        print('\n✅ CHUNK 1 GREEN — ready for Chunk 2 (spin engine)')
        sys.exit(0)


if __name__ == '__main__':
    main()