"""
Smoke Test — v3 Chunk 5 (Crypto Payout Pipeline — Mock Mode)
================================================================

Verifies the full crypto withdrawal lifecycle in mock mode:

  1. Provider dispatch: get_provider('crypto') returns NOWPaymentsPayoutProvider
  2. Mock mode initiate_payout returns 'completed' instantly with tx_hash
  3. Crypto withdrawal: request → approve → process → completed
  4. tx_hash populated on the Withdrawal record
  5. Crypto withdrawal failure: refunds CRYPTO_WITHDRAW (not naira)
  6. Crypto rejection refunds CRYPTO_WITHDRAW (not naira)
  7. Bank withdrawal flow still works (regression check)
  8. Bank rejection refunds NAIRA_WITHDRAW
  9. Mock provider failure path (PayoutProviderError → _mark_failed)
  10. Idempotency: complete_from_payout_webhook is safe to re-run

USAGE:
    docker compose exec api python smoke_test_chunk_5.py

NOTE: This test runs in mock mode. Make sure NOWPAYMENTS_PAYOUT_MODE=mock
(or unset — mock is the default).
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
from apps.withdrawals.services import WithdrawalService
from apps.withdrawals.providers import get_provider
from apps.withdrawals.providers.nowpayments_payout import (
    NOWPaymentsPayoutProvider,
)
from apps.withdrawals.providers.base import PayoutProviderError
from apps.kyc.models import BankAccount, KYCProfile
from apps.settings_app.services import set_setting, invalidate_cache
from apps.settings_app.models import SettingKey

SMOKE_PREFIX = 'v3c5_'
SMOKE_TID = 900500001
VALID_TRC20 = 'TS4mw7FvDqCWBvMJonTJGwRUxuP7s3R7NH'


def cleanup():
    try:
        u = User.objects.get(telegram_id=SMOKE_TID)
        Withdrawal.objects.filter(user=u).delete()
        CryptoWallet.objects.filter(user=u).delete()
        BankAccount.objects.filter(user=u).delete()
        KYCProfile.objects.filter(user=u).delete()
        Transaction.objects.filter(user=u).delete()
        u.delete()
    except User.DoesNotExist:
        pass


def make_user_with_kyc():
    user = User.objects.create(
        telegram_id=SMOKE_TID,
        first_name='V3C5',
        last_name='SmokeUser',
        username=f'{SMOKE_PREFIX}user',
    )

    # Approved KYC (so eligibility passes)
    try:
        KYCProfile.objects.create(
            user=user,
            full_name='V3C5 SMOKE',
            nin='12345678901',
            date_of_birth='1990-01-01',
            personal_info_status='verified',
            document_status='verified',
            bank_account_status='verified',
            verified_at=timezone.now(),
        )
    except Exception:
        # Fall back: minimum required fields only
        profile = KYCProfile.objects.create(user=user)
        for fld in ['personal_info_status', 'document_status', 'bank_account_status']:
            if hasattr(profile, fld):
                setattr(profile, fld, 'verified')
        if hasattr(profile, 'verified_at'):
            profile.verified_at = timezone.now()
        profile.save()

    return user


def make_bank_account(user):
    return BankAccount.objects.create(
        user=user,
        bank_code='058',
        bank_name='GTBank',
        account_number='0123456789',
        account_name='V3C5 SMOKE',
        is_default=True,
        is_active=True,
        verified_at=timezone.now(),
    )


def credit_balance(user, balance_type, amount):
    WalletService.credit(
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

def test_provider_dispatch():
    section('1. Provider dispatch by rail')

    bank_provider = get_provider('bank')
    crypto_provider = get_provider('crypto')

    check(bank_provider is not None, 'get_provider("bank") returns a provider')
    check(crypto_provider is not None, 'get_provider("crypto") returns a provider')
    check(
        crypto_provider.__class__.__name__ == 'NOWPaymentsPayoutProvider',
        f'crypto provider is NOWPaymentsPayoutProvider (got {crypto_provider.__class__.__name__})'
    )

    # Backward compat
    default_provider = get_provider()
    check(default_provider is not None, 'get_provider() (no arg) returns bank')


def test_mock_payout_succeeds():
    section('2. Mock payout returns "completed" + tx_hash')

    user = make_user_with_kyc()
    credit_balance(user, Transaction.BalanceType.CRYPTO_WITHDRAW, '100')

    # Build a stub withdrawal record to feed the provider (don't actually
    # submit — just exercise the provider directly)
    w = Withdrawal(
        user=user,
        rail=Withdrawal.Rail.CRYPTO,
        currency=Withdrawal.Currency.USDT,
        wallet_address=VALID_TRC20,
        network='TRC20',
        amount=Decimal('10'),
        net_amount=Decimal('10'),
        reference='wd_mock_test_001',
        status=Withdrawal.Status.PROCESSING,
    )
    # We don't save it — just pass to provider

    provider = NOWPaymentsPayoutProvider()
    result = provider.initiate_payout(w)

    check(result is not None, 'initiate_payout returned a result')
    check(result.status == 'completed',
          f'mock status = completed (got {result.status})')
    check(result.payout_id.startswith('MOCK_PAYOUT_'),
          f'mock payout_id has MOCK_ prefix (got {result.payout_id})')
    check(result.tx_hash.startswith('MOCK_TX_'),
          f'mock tx_hash has MOCK_ prefix (got {result.tx_hash[:20]})')
    check(len(result.tx_hash) > 30,
          f'mock tx_hash is reasonably long (got len={len(result.tx_hash)})')


def test_crypto_full_lifecycle():
    section('3. Crypto withdrawal: request → approve → process → completed')

    set_crypto_flag(True)
    user = User.objects.get(telegram_id=SMOKE_TID)

    # Submit
    withdrawal = WithdrawalService.request(
        user=user,
        amount=Decimal('20'),
        rail='crypto',
        wallet_address=VALID_TRC20,
        network='TRC20',
    )
    check(withdrawal.status == 'pending_review',
          f'After request: pending_review (got {withdrawal.status})')

    # Admin approves
    admin = user  # use same user as admin for this test
    WithdrawalService.approve(withdrawal, admin, notes='smoke test approval')

    withdrawal.refresh_from_db()
    # After approve: status becomes pending → process is enqueued via on_commit
    # In tests, on_commit doesn't auto-fire in non-test transactions —
    # we may need to call process directly to simulate the worker picking it up

    # Try to detect both cases:
    if withdrawal.status == 'completed':
        check(True, 'on_commit fired and process completed immediately')
    else:
        # Call process directly as the Celery worker would
        try:
            WithdrawalService.process(withdrawal)
        except Exception as e:
            check(False, f'process() crashed: {type(e).__name__}: {e}')

    withdrawal.refresh_from_db()

    check(withdrawal.status == 'completed',
          f'Status after process: completed (got {withdrawal.status})')
    check(bool(withdrawal.tx_hash),
          f'tx_hash populated (got {withdrawal.tx_hash[:30] if withdrawal.tx_hash else "EMPTY"})')
    check(withdrawal.tx_hash.startswith('MOCK_TX_'),
          f'tx_hash has MOCK_ prefix (got {withdrawal.tx_hash[:20]})')
    check(withdrawal.completed_at is not None,
          'completed_at set')


def test_crypto_rejection_refunds_crypto_withdraw():
    section('4. Crypto rejection refunds CRYPTO_WITHDRAW (not naira)')

    set_crypto_flag(True)
    user = User.objects.get(telegram_id=SMOKE_TID)
    credit_balance(user, Transaction.BalanceType.CRYPTO_WITHDRAW, '50')

    # Submit
    w = WithdrawalService.request(
        user=user,
        amount=Decimal('10'),
        rail='crypto',
        wallet_address=VALID_TRC20,
        network='TRC20',
    )

    before = WalletService.get_wallet_summary(user)

    # Admin rejects
    admin = user
    WithdrawalService.reject(w, admin, reason='Suspicious — smoke test')

    after = WalletService.get_wallet_summary(user)
    w.refresh_from_db()

    check(w.status == 'rejected', f'status = rejected (got {w.status})')

    crypto_w_gain = (Decimal(after['crypto_withdraw_balance'])
                     - Decimal(before['crypto_withdraw_balance']))
    naira_w_change = (Decimal(after['naira_withdraw_balance'])
                      - Decimal(before['naira_withdraw_balance']))

    check(crypto_w_gain == Decimal('10'),
          f'10 USDT refunded to crypto_withdraw (got gain={crypto_w_gain})')
    check(naira_w_change == 0,
          f'naira_withdraw UNCHANGED (delta={naira_w_change})')


def test_bank_withdrawal_still_works():
    section('5. Bank withdrawal regression — still goes to bank rail')

    user = User.objects.get(telegram_id=SMOKE_TID)
    credit_balance(user, Transaction.BalanceType.NAIRA_WITHDRAW, '5000')
    bank = make_bank_account(user)

    before_balance = WalletService.get_naira_withdraw_balance(user)

    w = WithdrawalService.request(
        user=user,
        amount=Decimal('1500'),
        rail='bank',
        bank_account=bank,
    )

    after_balance = WalletService.get_naira_withdraw_balance(user)

    check(w.rail == 'bank', f'rail = bank (got {w.rail})')
    check(w.currency == 'NGN', f'currency = NGN')
    check(w.bank_account is not None, 'bank_account set')
    check(w.wallet_address == '', 'wallet_address empty for bank')
    check((before_balance - after_balance) == Decimal('1500'),
          f'1500 debited from naira_withdraw')


def test_bank_rejection_refunds_naira_withdraw():
    section('6. Bank rejection refunds NAIRA_WITHDRAW')

    user = User.objects.get(telegram_id=SMOKE_TID)
    bank = BankAccount.objects.filter(user=user).first()

    # Make a fresh bank withdrawal that's large enough to need review
    credit_balance(user, Transaction.BalanceType.NAIRA_WITHDRAW, '50000')

    w = WithdrawalService.request_with_manual_review(
        user=user,
        amount=Decimal('20000'),
        rail='bank',
        bank_account=bank,
    )
    check(w.status == 'pending_review', 'Submitted as pending_review')

    before = WalletService.get_wallet_summary(user)

    # Reject
    admin = user
    WithdrawalService.reject(w, admin, reason='Test rejection')

    after = WalletService.get_wallet_summary(user)
    w.refresh_from_db()

    naira_w_gain = (Decimal(after['naira_withdraw_balance'])
                    - Decimal(before['naira_withdraw_balance']))
    crypto_w_change = (Decimal(after['crypto_withdraw_balance'])
                       - Decimal(before['crypto_withdraw_balance']))

    check(naira_w_gain == Decimal('20000'),
          f'20000 refunded to naira_withdraw')
    check(crypto_w_change == 0, 'crypto_withdraw untouched')


def test_payout_failure_refunds():
    section('7. Provider failure → _mark_failed → refund CRYPTO_WITHDRAW')

    set_crypto_flag(True)
    user = User.objects.get(telegram_id=SMOKE_TID)
    credit_balance(user, Transaction.BalanceType.CRYPTO_WITHDRAW, '100')

    # Create a withdrawal directly (bypass approve flow for testing)
    w = WithdrawalService.request(
        user=user,
        amount=Decimal('15'),
        rail='crypto',
        wallet_address=VALID_TRC20,
        network='TRC20',
    )

    before = WalletService.get_crypto_withdraw_balance(user)

    # Simulate provider failure by calling _mark_failed directly
    WithdrawalService._mark_failed(w, 'Simulated provider failure for testing')

    w.refresh_from_db()
    after = WalletService.get_crypto_withdraw_balance(user)

    check(w.status == 'failed', f'status = failed (got {w.status})')
    check(bool(w.failure_reason), 'failure_reason populated')
    check(w.refund_transaction is not None, 'refund_transaction linked')
    check((after - before) == Decimal('15'),
          f'15 USDT refunded to crypto_withdraw (got delta={after - before})')


def test_complete_from_payout_webhook_idempotent():
    section('8. complete_from_payout_webhook is idempotent')

    set_crypto_flag(True)
    user = User.objects.get(telegram_id=SMOKE_TID)
    credit_balance(user, Transaction.BalanceType.CRYPTO_WITHDRAW, '50')

    # Build a withdrawal in PROCESSING state
    w = Withdrawal.objects.create(
        user=user,
        rail=Withdrawal.Rail.CRYPTO,
        currency=Withdrawal.Currency.USDT,
        wallet_address=VALID_TRC20,
        network='TRC20',
        amount=Decimal('5'),
        net_amount=Decimal('5'),
        reference=f'wd_idempotency_test_{uuid.uuid4().hex[:8]}',
        status=Withdrawal.Status.PROCESSING,
        requires_review=True,
    )

    # First call
    WithdrawalService.complete_from_payout_webhook(w, tx_hash='REAL_TX_HASH_001')
    w.refresh_from_db()
    first_completed_at = w.completed_at
    first_tx_hash = w.tx_hash

    check(w.status == 'completed', 'First call: status=completed')
    check(w.tx_hash == 'REAL_TX_HASH_001', 'First call: tx_hash stored')
    check(first_completed_at is not None, 'First call: completed_at set')

    # Second call (replay) — should be no-op
    WithdrawalService.complete_from_payout_webhook(w, tx_hash='SHOULD_NOT_OVERWRITE')
    w.refresh_from_db()

    check(w.completed_at == first_completed_at,
          'Replay: completed_at unchanged')
    check(w.tx_hash == first_tx_hash,
          f'Replay: tx_hash unchanged (got {w.tx_hash})')


def test_mock_mode_explicit_setting():
    section('9. NOWPAYMENTS_PAYOUT_MODE setting respected')

    from django.conf import settings
    mode = getattr(settings, 'NOWPAYMENTS_PAYOUT_MODE', 'mock')
    check(mode in ('mock', 'live'),
          f'Mode is mock or live (got {mode!r})')

    # The smoke test should be running in mock mode
    if mode != 'mock':
        print(f'    NOTE: Running in {mode} mode — some tests may behave differently')


# ═════════════════════════════════════════════════════════════════════════
# RUNNER
# ═════════════════════════════════════════════════════════════════════════

def main():
    print('=' * 70)
    print('  SPIN REWARDS v3 — CHUNK 5 SMOKE TEST (CRYPTO PAYOUT — MOCK MODE)')
    print('=' * 70)

    cleanup()
    set_crypto_flag(False)  # start with flag off, tests will toggle as needed

    try:
        test_provider_dispatch()
        test_mock_payout_succeeds()
        test_crypto_full_lifecycle()
        test_crypto_rejection_refunds_crypto_withdraw()
        test_bank_withdrawal_still_works()
        test_bank_rejection_refunds_naira_withdraw()
        test_payout_failure_refunds()
        test_complete_from_payout_webhook_idempotent()
        test_mock_mode_explicit_setting()
    finally:
        cleanup()
        set_crypto_flag(False)

    print(f'\n{"=" * 70}')
    print(f'  RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print('=' * 70)

    if TESTS_FAILED:
        print('\nFAILED:')
        for label in FAIL_DETAILS:
            print(f'  ✗ {label}')
        print('\n❌ CHUNK 5 FAILED — fix before proceeding to Chunk 6')
        sys.exit(1)
    else:
        print('\n✅ CHUNK 5 GREEN — ready for Chunk 6 (admin endpoints)')
        sys.exit(0)


if __name__ == '__main__':
    main()