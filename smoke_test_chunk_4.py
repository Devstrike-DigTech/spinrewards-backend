"""
Smoke Test — v3 Chunk 4 (Withdrawals — Dual Rail)
====================================================

Verifies:
  1. Bank rail still works (debit naira_withdraw, NGN min)
  2. Crypto rail rejected when flag=0 (CRYPTO_WITHDRAWAL_DISABLED)
  3. Crypto rail works when flag=1 (debit crypto_withdraw, USDT min)
  4. Crypto withdrawals are ALWAYS forced to pending_review
  5. Invalid TRC-20 address rejected with INVALID_WALLET_ADDRESS
  6. Unsupported network rejected
  7. Insufficient funds in correct balance (bank: naira_withdraw; crypto: crypto_withdraw)
  8. Min validation per rail
  9. CryptoWalletService — save, list, deactivate
  10. Address validation: edge cases (length, prefix, invalid chars)

USAGE:
    docker compose exec api python smoke_test_chunk_4.py
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

from apps.users.models import User
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService
from apps.withdrawals.models import Withdrawal, CryptoWallet
from apps.withdrawals.services import WithdrawalService, WithdrawalServiceError
from apps.withdrawals.crypto_wallet_service import (
    CryptoWalletService, CryptoWalletError,
)
from apps.kyc.models import BankAccount
from apps.settings_app.services import set_setting, invalidate_cache
from apps.settings_app.models import SettingKey

SMOKE_PREFIX = 'v3c4_'
SMOKE_TID = 900400001

# Known valid TRC-20 USDT address format (34 chars, starts with T, base58 chars)
VALID_TRC20_ADDRESS = 'TS4mw7FvDqCWBvMJonTJGwRUxuP7s3R7NH'


def cleanup():
    try:
        u = User.objects.get(telegram_id=SMOKE_TID)
        Withdrawal.objects.filter(user=u).delete()
        CryptoWallet.objects.filter(user=u).delete()
        BankAccount.objects.filter(user=u).delete()
        Transaction.objects.filter(user=u).delete()
        u.delete()
    except User.DoesNotExist:
        pass


def make_user():
    return User.objects.create(
        telegram_id=SMOKE_TID,
        first_name='V3C4',
        last_name='SmokeUser',
        username=f'{SMOKE_PREFIX}user',
    )
def make_approved_kyc(user):
    """Seed an approved KYC profile so withdrawal eligibility passes."""
    from apps.kyc.models import KYCProfile
    from django.utils import timezone

    # Try to create with common field names; adjust if your model differs
    try:
        profile = KYCProfile.objects.create(
            user=user,
            full_name='V3C4 SMOKE',
            nin='12345678901',
            date_of_birth='1990-01-01',
            personal_info_status='verified',
            document_status='verified',
            bank_account_status='verified',
            verified_at=timezone.now(),
        )
    except Exception:
        # Fall back if model has different fields
        profile = KYCProfile.objects.create(user=user)
        # Try to set common fields
        for fld in ['personal_info_status', 'document_status', 'bank_account_status']:
            if hasattr(profile, fld):
                setattr(profile, fld, 'verified')
        if hasattr(profile, 'verified_at'):
            profile.verified_at = timezone.now()
        profile.save()
    return profile


def make_bank_account(user):
    return BankAccount.objects.create(
        user=user,
        bank_code='058',
        bank_name='GTBank',
        account_number='0123456789',
        account_name='V3C4 SMOKE',
        is_default=True,
        is_active=True,
        verified_at=timezone.now(),
    )


def credit_balance(user, balance_type, amount):
    return WalletService.credit(
        user=user,
        amount=Decimal(str(amount)),
        balance_type=balance_type,
        tx_type=Transaction.Type.DEPOSIT,
        reference_id=f'{SMOKE_PREFIX}seed-{uuid.uuid4().hex[:8]}',
    )


def set_crypto_flag(enabled: bool):
    set_setting(SettingKey.CRYPTO_WITHDRAWAL_ENABLED,
                Decimal('1') if enabled else Decimal('0'))
    invalidate_cache()


# ═════════════════════════════════════════════════════════════════════════
# TESTS
# ═════════════════════════════════════════════════════════════════════════

def test_bank_rail_still_works():
    section('1. Bank rail — debit naira_withdraw, NGN min')

    user = make_user()
    make_approved_kyc(user) 
    bank = make_bank_account(user)
    credit_balance(user, Transaction.BalanceType.NAIRA_WITHDRAW, '10000')

    before = WalletService.get_wallet_summary(user)

    try:
        w = WithdrawalService.request(
            user=user,
            amount=Decimal('1500'),
            rail='bank',
            bank_account=bank,
        )
        check(True, 'Bank withdrawal request succeeded')
        check(w.rail == 'bank', f'rail=bank (got {w.rail})')
        check(w.currency == 'NGN', f'currency=NGN (got {w.currency})')
        check(w.bank_account_id == bank.id, 'bank_account linked')
        check(w.wallet_address == '', 'wallet_address empty for bank')
    except Exception as e:
        check(False, f'Bank withdrawal failed: {type(e).__name__}: {e}')
        return

    after = WalletService.get_wallet_summary(user)
    naira_w_drop = (Decimal(before['naira_withdraw_balance'])
                    - Decimal(after['naira_withdraw_balance']))
    check(naira_w_drop == Decimal('1500'),
          f'1500 debited from naira_withdraw (got drop={naira_w_drop})')

    # crypto_withdraw untouched
    crypto_w_change = (Decimal(after['crypto_withdraw_balance'])
                       - Decimal(before['crypto_withdraw_balance']))
    check(crypto_w_change == 0, 'crypto_withdraw untouched by bank withdrawal')


def test_crypto_rail_disabled_by_default():
    section('2. Crypto rail rejected when flag=0')

    set_crypto_flag(False)

    user = User.objects.get(telegram_id=SMOKE_TID)
    credit_balance(user, Transaction.BalanceType.CRYPTO_WITHDRAW, '100')

    try:
        WithdrawalService.request(
            user=user,
            amount=Decimal('20'),
            rail='crypto',
            wallet_address=VALID_TRC20_ADDRESS,
            network='TRC20',
        )
        check(False, 'Should have raised CRYPTO_WITHDRAWAL_DISABLED')
    except WithdrawalServiceError as e:
        check(getattr(e, 'code', None) == 'CRYPTO_WITHDRAWAL_DISABLED',
              f'code=CRYPTO_WITHDRAWAL_DISABLED (got {getattr(e, "code", None)})')


def test_crypto_rail_works_when_enabled():
    section('3. Crypto rail works when flag=1')

    set_crypto_flag(True)

    user = User.objects.get(telegram_id=SMOKE_TID)
    # already has 100 USDT in crypto_withdraw from previous test

    before = WalletService.get_wallet_summary(user)

    try:
        w = WithdrawalService.request(
            user=user,
            amount=Decimal('20'),
            rail='crypto',
            wallet_address=VALID_TRC20_ADDRESS,
            network='TRC20',
        )
        check(True, 'Crypto withdrawal request succeeded')
        check(w.rail == 'crypto', f'rail=crypto (got {w.rail})')
        check(w.currency == 'USDT', f'currency=USDT (got {w.currency})')
        check(w.wallet_address == VALID_TRC20_ADDRESS,
              'wallet_address stored')
        check(w.network == 'TRC20', f'network=TRC20 (got {w.network})')
        check(w.bank_account is None, 'bank_account is None for crypto')

        # Force manual review per D7
        check(w.status == 'pending_review',
              f'status=pending_review (forced for crypto) (got {w.status})')
        check(w.requires_review is True,
              f'requires_review=True (got {w.requires_review})')

    except Exception as e:
        check(False, f'Crypto withdrawal failed: {type(e).__name__}: {e}')
        return

    after = WalletService.get_wallet_summary(user)
    crypto_w_drop = (Decimal(before['crypto_withdraw_balance'])
                     - Decimal(after['crypto_withdraw_balance']))
    check(crypto_w_drop == Decimal('20'),
          f'20 debited from crypto_withdraw (got drop={crypto_w_drop})')

    # naira_withdraw untouched
    naira_w_change = (Decimal(after['naira_withdraw_balance'])
                      - Decimal(before['naira_withdraw_balance']))
    check(naira_w_change == 0, 'naira_withdraw untouched by crypto withdrawal')


def test_crypto_always_pending_review():
    section('4. Crypto withdrawals ALWAYS go to manual review')

    set_crypto_flag(True)
    user = User.objects.get(telegram_id=SMOKE_TID)

    # Even a small amount that would auto-process for bank goes to review
    credit_balance(user, Transaction.BalanceType.CRYPTO_WITHDRAW, '50')

    w = WithdrawalService.request(
        user=user,
        amount=Decimal('5'),    # smallest possible
        rail='crypto',
        wallet_address=VALID_TRC20_ADDRESS,
        network='TRC20',
    )
    check(w.status == 'pending_review',
          f'Tiny crypto withdrawal still pending_review (got {w.status})')


def test_invalid_trc20_address():
    section('5. Invalid TRC-20 address rejected')

    set_crypto_flag(True)
    user = User.objects.get(telegram_id=SMOKE_TID)

    bad_addresses = [
        '',                                    # empty
        'XYZ',                                 # too short
        '1' * 34,                              # right length, wrong prefix
        'T' + '0' * 33,                        # has 0 (not in base58)
        'T' + 'I' * 33,                        # has I (not in base58)
        'T' + 'l' * 33,                        # has l (not in base58)
        'T' + 'O' * 33,                        # has O (not in base58)
        'T' + 'a' * 35,                        # too long
        'T' + 'a' * 32,                        # too short
    ]

    rejected = 0
    for addr in bad_addresses:
        try:
            WithdrawalService.request(
                user=user, amount=Decimal('10'),
                rail='crypto', wallet_address=addr, network='TRC20',
            )
        except WithdrawalServiceError as e:
            if getattr(e, 'code', None) in (
                'INVALID_WALLET_ADDRESS', 'MISSING_WALLET_ADDRESS',
            ):
                rejected += 1
        except CryptoWalletError:
            rejected += 1

    check(rejected == len(bad_addresses),
          f'All {len(bad_addresses)} bad addresses rejected (got {rejected})')


def test_unsupported_network():
    section('6. Unsupported network rejected')

    set_crypto_flag(True)
    user = User.objects.get(telegram_id=SMOKE_TID)

    try:
        WithdrawalService.request(
            user=user,
            amount=Decimal('10'),
            rail='crypto',
            wallet_address=VALID_TRC20_ADDRESS,
            network='ERC20',  # not supported yet
        )
        check(False, 'Should have rejected ERC20')
    except WithdrawalServiceError as e:
        check(getattr(e, 'code', None) == 'UNSUPPORTED_NETWORK',
              f'code=UNSUPPORTED_NETWORK (got {getattr(e, "code", None)})')


def test_insufficient_funds_per_rail():
    section('7. Insufficient funds — correct balance checked')

    set_crypto_flag(True)
    user = User.objects.get(telegram_id=SMOKE_TID)

    # The user has ~8500 NGN in naira_withdraw after test 1 (started with 10000, withdrew 1500)
    # And some USDT in crypto_withdraw

    # Get current actual balances
    naira_w = WalletService.get_naira_withdraw_balance(user)
    crypto_w = WalletService.get_crypto_withdraw_balance(user)

    bank = BankAccount.objects.filter(user=user, is_active=True).first()

    # 1) Bank: try to withdraw 100 MORE than user has (small overage — below daily limit)
    overage_ngn = naira_w + Decimal('100')
    try:
        WithdrawalService.request(
            user=user, amount=overage_ngn,
            rail='bank', bank_account=bank,
        )
        check(False, f'Bank overspend ({overage_ngn}) should fail')
    except WithdrawalServiceError as e:
        check(getattr(e, 'code', None) == 'INSUFFICIENT_FUNDS',
              f'bank INSUFFICIENT_FUNDS (got {getattr(e, "code", None)})')

    # 2) Crypto: try to withdraw 5 MORE than user has
    overage_usdt = crypto_w + Decimal('5')
    try:
        WithdrawalService.request(
            user=user, amount=overage_usdt,
            rail='crypto', wallet_address=VALID_TRC20_ADDRESS, network='TRC20',
        )
        check(False, f'Crypto overspend ({overage_usdt}) should fail')
    except WithdrawalServiceError as e:
        check(getattr(e, 'code', None) == 'INSUFFICIENT_FUNDS',
              f'crypto INSUFFICIENT_FUNDS (got {getattr(e, "code", None)})')


def test_per_rail_minimums():
    section('8. Per-rail min withdrawal enforced')

    set_crypto_flag(True)
    user = User.objects.get(telegram_id=SMOKE_TID)

    # Bank min = 1000 NGN
    bank = BankAccount.objects.filter(user=user, is_active=True).first()
    try:
        WithdrawalService.request(
            user=user, amount=Decimal('500'),
            rail='bank', bank_account=bank,
        )
        check(False, 'Bank ₦500 should be below min')
    except WithdrawalServiceError as e:
        check(getattr(e, 'code', None) == 'BELOW_MIN_WITHDRAWAL',
              f'bank BELOW_MIN_WITHDRAWAL (got {getattr(e, "code", None)})')

    # Crypto min = 5 USDT
    try:
        WithdrawalService.request(
            user=user, amount=Decimal('1'),
            rail='crypto', wallet_address=VALID_TRC20_ADDRESS, network='TRC20',
        )
        check(False, 'Crypto $1 should be below min')
    except WithdrawalServiceError as e:
        check(getattr(e, 'code', None) == 'BELOW_MIN_WITHDRAWAL',
              f'crypto BELOW_MIN_WITHDRAWAL (got {getattr(e, "code", None)})')


def test_crypto_wallet_crud():
    section('9. CryptoWalletService — save / list / deactivate')

    user = User.objects.get(telegram_id=SMOKE_TID)

    # Save a new wallet
    wallet1 = CryptoWalletService.save(
        user=user,
        address=VALID_TRC20_ADDRESS,
        network='TRC20',
        label='Binance hot',
    )
    check(wallet1.id is not None, 'Wallet saved with ID')
    check(wallet1.is_default is True, 'First wallet becomes default automatically')
    check(wallet1.is_active is True, 'New wallet is active')
    check(wallet1.label == 'Binance hot', 'Label saved')

    # Try saving the same address again — should return existing
    wallet1_again = CryptoWalletService.save(
        user=user, address=VALID_TRC20_ADDRESS, network='TRC20',
    )
    check(wallet1.id == wallet1_again.id, 'Re-save returns existing wallet')

    # List
    wallets = CryptoWalletService.list_for_user(user)
    check(wallets.count() == 1, f'list returns 1 wallet (got {wallets.count()})')

    # Get default
    default = CryptoWalletService.get_default(user, 'TRC20')
    check(default is not None and default.id == wallet1.id,
          'get_default returns the saved wallet')

    # Deactivate
    CryptoWalletService.deactivate(user, wallet1.id)
    wallets_after = CryptoWalletService.list_for_user(user)
    check(wallets_after.count() == 0,
          'Deactivated wallet hidden from active list')


def test_address_validation_unit():
    section('10. Address validation — TRC-20 format rules')

    valid_addresses = [
        'TS4mw7FvDqCWBvMJonTJGwRUxuP7s3R7NH',  # real test address
        'T' + 'A' * 33,                          # all A
        'T' + '9' * 33,                          # all 9
    ]
    invalid_addresses = [
        ('', 'empty'),
        ('T', 'too short'),
        ('T' + 'a' * 35, 'too long'),
        ('A' + 'a' * 33, 'wrong prefix'),
        ('T0' + 'a' * 32, 'has 0'),
        ('TO' + 'a' * 32, 'has O'),
        ('Tl' + 'a' * 32, 'has l'),
        ('TI' + 'a' * 32, 'has I'),
    ]

    for addr in valid_addresses:
        try:
            CryptoWalletService.validate_address(addr, 'TRC20')
            check(True, f'valid: {addr[:8]}...')
        except CryptoWalletError as e:
            check(False, f'incorrectly rejected valid address: {addr} ({e})')

    for addr, reason in invalid_addresses:
        try:
            CryptoWalletService.validate_address(addr, 'TRC20')
            check(False, f'should reject {reason!r}: {addr[:16]!r}')
        except CryptoWalletError:
            check(True, f'rejects {reason}')


# ═════════════════════════════════════════════════════════════════════════
# RUNNER
# ═════════════════════════════════════════════════════════════════════════

def main():
    print('=' * 70)
    print('  SPIN REWARDS v3 — CHUNK 4 SMOKE TEST (WITHDRAWALS DUAL RAIL)')
    print('=' * 70)

    cleanup()
    set_crypto_flag(False)  # start with flag off

    try:
        test_bank_rail_still_works()
        test_crypto_rail_disabled_by_default()
        test_crypto_rail_works_when_enabled()
        test_crypto_always_pending_review()
        test_invalid_trc20_address()
        test_unsupported_network()
        test_insufficient_funds_per_rail()
        test_per_rail_minimums()
        test_crypto_wallet_crud()
        test_address_validation_unit()
    finally:
        cleanup()
        set_crypto_flag(False)  # leave it off

    print(f'\n{"=" * 70}')
    print(f'  RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print('=' * 70)

    if TESTS_FAILED:
        print('\nFAILED:')
        for label in FAIL_DETAILS:
            print(f'  ✗ {label}')
        print('\n❌ CHUNK 4 FAILED — fix before proceeding to Chunk 5')
        sys.exit(1)
    else:
        print('\n✅ CHUNK 4 GREEN — ready for Chunk 5 (NowPayments payout)')
        sys.exit(0)


if __name__ == '__main__':
    main()