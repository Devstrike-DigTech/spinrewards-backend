"""
Withdrawal module smoke test.

Stub patterns:
  account_number ending '9999' → recipient creation fails
  reference containing '9999'  → transfer initiation fails
  reference containing '0000'  → transfer stays pending

Usage:
    docker compose exec api python smoke_test_withdrawals.py
"""
import os
import sys
from datetime import date
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.test import override_settings
from django.utils import timezone

from apps.kyc.models import BankAccount, KYCProfile
from apps.users.models import User
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService
from apps.withdrawals.models import Withdrawal
from apps.withdrawals.services import WithdrawalService, WithdrawalServiceError


TESTS_RUN = 0
TESTS_PASSED = 0
TESTS_FAILED = 0


def check(condition, label):
    global TESTS_RUN, TESTS_PASSED, TESTS_FAILED
    TESTS_RUN += 1
    if condition:
        TESTS_PASSED += 1
        print(f'  ✓ {label}')
    else:
        TESTS_FAILED += 1
        print(f'  ✗ {label}')


def section(title):
    print(f'\n{"=" * 60}')
    print(title)
    print(f'{"=" * 60}')


def cleanup(telegram_id):
    try:
        u = User.objects.get(telegram_id=telegram_id)
        Withdrawal.objects.filter(user=u).delete()
        BankAccount.objects.filter(user=u).delete()
        KYCProfile.objects.filter(user=u).delete()
        u.delete()
    except User.DoesNotExist:
        pass


def make_kyc_user(
    telegram_id,
    cash_balance=Decimal('50000'),
    account_number='1234567890',
    bank_code='058',
):
    cleanup(telegram_id)
    user = User.objects.create_user(
        telegram_id=telegram_id,
        first_name='WdSmoke',
        username=f'wd_smoke_{telegram_id}',
    )

    KYCProfile.objects.create(
        user=user,
        full_name='JOHN DOE',
        nin='12345678901',
        bvn='12345678901',
        date_of_birth=date(1990, 1, 1),
        phone_number='08012345678',
        personal_info_status=KYCProfile.SectionStatus.VERIFIED,
        bank_account_status=KYCProfile.SectionStatus.VERIFIED,
        document_status=KYCProfile.SectionStatus.VERIFIED,
        submitted_at=timezone.now(),
    )

    BankAccount.objects.create(
        user=user,
        bank_code=bank_code,
        bank_name='GTBank',
        account_number=account_number,
        account_name='JOHN DOE',
        is_active=True,
        verified_at=timezone.now(),
    )

    if cash_balance > 0:
        WalletService.credit(
            user=user, amount=cash_balance, balance_type='cash',
            tx_type=Transaction.Type.WIN,
            reference_id=f'smoke_init_{telegram_id}',
            metadata={'source': 'smoke_test'},
        )
    return user


def make_unverified_user(telegram_id):
    cleanup(telegram_id)
    user = User.objects.create_user(
        telegram_id=telegram_id,
        first_name='Unverified',
        username=f'unverified_{telegram_id}',
    )
    WalletService.credit(
        user=user, amount=Decimal('5000'), balance_type='cash',
        tx_type=Transaction.Type.WIN,
        reference_id=f'unverified_init_{telegram_id}',
        metadata={'source': 'smoke_test'},
    )
    return user


# ─── Tests ───────────────────────────────────────────────────────────────────

@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_happy_path_auto_payout():
    section('TEST 1: Tiered flow — auto-payout under threshold')

    user = make_kyc_user(800001000, cash_balance=Decimal('20000'))
    cash_before = WalletService.get_balance(user, 'cash')

    withdrawal = WithdrawalService.request(user, Decimal('5000'))
    withdrawal.refresh_from_db()

    check(withdrawal is not None, 'withdrawal created')
    check(not withdrawal.requires_review, 'amount under threshold → no review')
    check(not withdrawal.forced_manual_review, 'forced_manual_review is false')
    check(withdrawal.status in ['completed', 'processing'],
          f'auto-processed (status={withdrawal.status})')
    check(WalletService.get_balance(user, 'cash') == cash_before - Decimal('5000'),
          'cash debited')

    cleanup(800001000)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_tiered_manual_review():
    section('TEST 2: Tiered flow — amount ≥ ₦10k requires review')

    user = make_kyc_user(800001001, cash_balance=Decimal('50000'))
    withdrawal = WithdrawalService.request(user, Decimal('15000'))

    check(withdrawal.requires_review, 'requires_review flag set')
    check(not withdrawal.forced_manual_review, 'not flagged as forced')
    check(withdrawal.status == Withdrawal.Status.PENDING_REVIEW, 'status pending_review')
    check(WalletService.get_balance(user, 'cash') == Decimal('35000'),
          'cash debited even before approval')
    check(withdrawal.processing_at is None, 'not yet processing')

    cleanup(800001001)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_forced_manual_review_small_amount():
    section('TEST 3: Forced manual review — small amount still requires review')

    user = make_kyc_user(800001002, cash_balance=Decimal('20000'))

    # Even ₦2000 (well under threshold) goes to review
    withdrawal = WithdrawalService.request_with_manual_review(user, Decimal('2000'))

    check(withdrawal.requires_review, 'requires_review set')
    check(withdrawal.forced_manual_review, 'forced_manual_review flag set')
    check(withdrawal.status == Withdrawal.Status.PENDING_REVIEW,
          'status pending_review (not auto-processed)')
    check(WalletService.get_balance(user, 'cash') == Decimal('18000'),
          'cash still debited')

    cleanup(800001002)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_forced_manual_review_large_amount():
    section('TEST 4: Forced manual review — large amount works the same')

    user = make_kyc_user(800001003, cash_balance=Decimal('50000'))

    withdrawal = WithdrawalService.request_with_manual_review(user, Decimal('20000'))

    check(withdrawal.requires_review, 'requires_review set')
    check(withdrawal.forced_manual_review, 'forced_manual_review flag set')
    check(withdrawal.status == Withdrawal.Status.PENDING_REVIEW, 'status pending_review')

    cleanup(800001003)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_admin_approve():
    section('TEST 5: Admin approves manual review → processing')

    user = make_kyc_user(800001004, cash_balance=Decimal('50000'))
    admin = User.objects.create_user(
        telegram_id=800001999, first_name='Admin', username='admin_user',
    )

    withdrawal = WithdrawalService.request(user, Decimal('20000'))
    check(withdrawal.status == Withdrawal.Status.PENDING_REVIEW, 'pending review')

    withdrawal = WithdrawalService.approve(withdrawal, admin, notes='Looks good')
    withdrawal.refresh_from_db()

    check(withdrawal.status in ['completed', 'processing'],
          f'auto-processed after approval (status={withdrawal.status})')
    check(withdrawal.reviewed_by == admin, 'reviewed_by recorded')
    check(withdrawal.reviewed_at is not None, 'reviewed_at recorded')

    cleanup(800001004)
    cleanup(800001999)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_admin_approve_forced_review():
    section('TEST 6: Admin approves forced-review → processing')

    user = make_kyc_user(800001005, cash_balance=Decimal('20000'))
    admin = User.objects.create_user(
        telegram_id=800001998, first_name='Admin2', username='admin2',
    )

    withdrawal = WithdrawalService.request_with_manual_review(user, Decimal('3000'))
    check(withdrawal.status == Withdrawal.Status.PENDING_REVIEW, 'pending review')

    withdrawal = WithdrawalService.approve(withdrawal, admin)
    withdrawal.refresh_from_db()

    check(withdrawal.status in ['completed', 'processing'],
          'forced-review withdrawal processed after admin approval')
    check(withdrawal.forced_manual_review, 'forced flag preserved through lifecycle')

    cleanup(800001005)
    cleanup(800001998)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_admin_reject():
    section('TEST 7: Admin rejects → cash refunded')

    user = make_kyc_user(800001006, cash_balance=Decimal('50000'))
    admin = User.objects.create_user(
        telegram_id=800001997, first_name='Admin3', username='admin3',
    )

    withdrawal = WithdrawalService.request(user, Decimal('20000'))
    check(WalletService.get_balance(user, 'cash') == Decimal('30000'), 'cash debited')

    withdrawal = WithdrawalService.reject(withdrawal, admin, reason='Suspicious')
    withdrawal.refresh_from_db()

    check(withdrawal.status == Withdrawal.Status.REJECTED, 'status rejected')
    check(withdrawal.refund_transaction is not None, 'refund_transaction recorded')
    check(WalletService.get_balance(user, 'cash') == Decimal('50000'),
          'cash refunded')

    cleanup(800001006)
    cleanup(800001997)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_user_cancel():
    section('TEST 8: User cancels pending_review withdrawal')

    user = make_kyc_user(800001007, cash_balance=Decimal('50000'))

    # Forced manual review so status is pending_review even for small amount
    withdrawal = WithdrawalService.request_with_manual_review(user, Decimal('3000'))
    check(withdrawal.status == Withdrawal.Status.PENDING_REVIEW, 'pending review')

    withdrawal = WithdrawalService.cancel(user, withdrawal)
    withdrawal.refresh_from_db()

    check(withdrawal.status == Withdrawal.Status.CANCELLED, 'status cancelled')
    check(WalletService.get_balance(user, 'cash') == Decimal('50000'),
          'cash refunded after cancellation')

    cleanup(800001007)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_kyc_required():
    section('TEST 9: Without KYC → withdrawal rejected (both endpoints)')

    user = make_unverified_user(800001008)

    try:
        WithdrawalService.request(user, Decimal('2000'))
        check(False, 'tiered should reject unverified')
    except WithdrawalServiceError as e:
        check('kyc' in str(e).lower() or 'verification' in str(e).lower(),
              'tiered rejected with KYC error')

    try:
        WithdrawalService.request_with_manual_review(user, Decimal('2000'))
        check(False, 'forced-review should reject unverified')
    except WithdrawalServiceError as e:
        check('kyc' in str(e).lower() or 'verification' in str(e).lower(),
              'forced-review rejected with KYC error')

    check(WalletService.get_balance(user, 'cash') == Decimal('5000'),
          'cash unchanged after rejections')

    cleanup(800001008)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_validation_min_amount():
    section('TEST 10: Below minimum → rejected (both endpoints)')

    user = make_kyc_user(800001009, cash_balance=Decimal('5000'))

    try:
        WithdrawalService.request(user, Decimal('500'))
        check(False, 'tiered should reject below min')
    except WithdrawalServiceError as e:
        check('minimum' in str(e).lower(), 'tiered rejected below minimum')

    try:
        WithdrawalService.request_with_manual_review(user, Decimal('500'))
        check(False, 'forced-review should reject below min')
    except WithdrawalServiceError as e:
        check('minimum' in str(e).lower(), 'forced-review rejected below minimum')

    cleanup(800001009)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_max_per_txn():
    section('TEST 11: Above per-transaction max → rejected')

    user = make_kyc_user(800001010, cash_balance=Decimal('500000'))

    try:
        WithdrawalService.request(user, Decimal('150000'))
        check(False, 'should reject above max per txn')
    except WithdrawalServiceError as e:
        check('maximum' in str(e).lower(), 'rejected above max per transaction')

    cleanup(800001010)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_insufficient_balance():
    section('TEST 12: Insufficient cash balance → rejected')

    user = make_kyc_user(800001011, cash_balance=Decimal('1500'))

    try:
        WithdrawalService.request(user, Decimal('5000'))
        check(False, 'should reject insufficient cash')
    except WithdrawalServiceError as e:
        check('insufficient' in str(e).lower(), 'rejected insufficient cash')

    cleanup(800001011)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_daily_count_limit():
    section('TEST 13: Daily count limit (3 per day)')

    user = make_kyc_user(800001012, cash_balance=Decimal('100000'))

    for i in range(3):
        w = WithdrawalService.request(user, Decimal('2000'))
        check(w is not None, f'withdrawal {i+1} succeeded')

    try:
        WithdrawalService.request(user, Decimal('2000'))
        check(False, 'should reject 4th withdrawal')
    except WithdrawalServiceError as e:
        check('daily' in str(e).lower(), 'rejected exceeding daily count')

    cleanup(800001012)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_provider_recipient_failure():
    section('TEST 14: Provider recipient creation fails → withdrawal failed')

    user = make_kyc_user(
        800001013,
        cash_balance=Decimal('20000'),
        account_number='1234569999',  # ends 9999 → triggers stub failure
    )

    withdrawal = WithdrawalService.request(user, Decimal('3000'))
    withdrawal = WithdrawalService.process(withdrawal)
    withdrawal.refresh_from_db()

    check(withdrawal.status == Withdrawal.Status.FAILED, 'status failed')
    check('recipient' in withdrawal.failure_reason.lower(),
          'failure reason mentions recipient')
    check(withdrawal.refund_transaction is not None, 'refund recorded')
    check(WalletService.get_balance(user, 'cash') == Decimal('20000'),
          'cash refunded after failure')

    cleanup(800001013)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_idempotent_complete():
    section('TEST 15: complete() is idempotent')

    user = make_kyc_user(800001014, cash_balance=Decimal('20000'))
    withdrawal = WithdrawalService.request(user, Decimal('3000'))

    # Walk through to completion
    if withdrawal.status == Withdrawal.Status.PENDING:
        withdrawal = WithdrawalService.process(withdrawal)
    withdrawal.refresh_from_db()

    if withdrawal.status != Withdrawal.Status.COMPLETED:
        withdrawal = WithdrawalService.complete(withdrawal)

    check(withdrawal.status == Withdrawal.Status.COMPLETED, 'completed')

    # Idempotent re-call
    withdrawal2 = WithdrawalService.complete(withdrawal)
    check(withdrawal2.status == Withdrawal.Status.COMPLETED, 'idempotent re-call')

    cleanup(800001014)


@override_settings(WITHDRAWAL_PROVIDER='stub', CELERY_TASK_ALWAYS_EAGER=True)
def test_cant_cancel_after_processing():
    section('TEST 16: Cannot cancel after processing started')

    user = make_kyc_user(800001015, cash_balance=Decimal('20000'))

    # Tiered flow under threshold → auto-processed immediately
    withdrawal = WithdrawalService.request(user, Decimal('5000'))
    withdrawal.refresh_from_db()

    check(withdrawal.status in ['processing', 'completed'],
          'past pending_review state')

    try:
        WithdrawalService.cancel(user, withdrawal)
        check(False, 'should reject cancel after processing')
    except WithdrawalServiceError as e:
        check('cannot cancel' in str(e).lower(),
              'rejected cancel after processing')

    cleanup(800001015)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print('SPIN REWARDS — WITHDRAWALS SMOKE TEST')
    print('Testing both endpoints with stub provider.\n')

    try:
        test_happy_path_auto_payout()
        test_tiered_manual_review()
        test_forced_manual_review_small_amount()
        test_forced_manual_review_large_amount()
        test_admin_approve()
        test_admin_approve_forced_review()
        test_admin_reject()
        test_user_cancel()
        test_kyc_required()
        test_validation_min_amount()
        test_max_per_txn()
        test_insufficient_balance()
        test_daily_count_limit()
        test_provider_recipient_failure()
        test_idempotent_complete()
        test_cant_cancel_after_processing()
    finally:
        for tid in range(800001000, 800002000):
            cleanup(tid)

    print(f'\n{"=" * 60}')
    print(f'RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print(f'{"=" * 60}')

    if TESTS_FAILED:
        print('\n❌ WITHDRAWALS SMOKE TEST FAILED')
        sys.exit(1)
    else:
        print('\n✅ WITHDRAWALS SMOKE TEST PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()