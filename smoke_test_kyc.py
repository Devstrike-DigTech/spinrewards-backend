"""
KYC module smoke test.

Tests every code path of the KYC service using the stub provider.

Stub patterns:
  - NIN/BVN ending 9999 → provider error
  - NIN/BVN ending 0000 → returns 'MISMATCH NAME'
  - Account ending 9999 → bank not found
  - Account ending 0000 → returns 'WRONG PERSON'

Usage:
    docker compose exec api python smoke_test_kyc.py
"""
import os
import sys
from datetime import date

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.kyc.models import BankAccount, KYCDocument, KYCProfile
from apps.kyc.services import (
    KYCService,
    KYCServiceError,
    name_similarity,
    names_match,
)
from apps.users.models import User


# ─── Test infrastructure ─────────────────────────────────────────────────────

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
    try:
        u = User.objects.get(telegram_id=telegram_id)
        KYCDocument.objects.filter(user=u).delete()
        BankAccount.objects.filter(user=u).delete()
        KYCProfile.objects.filter(user=u).delete()
        u.delete()
    except User.DoesNotExist:
        pass


def make_test_user(telegram_id: int) -> User:
    cleanup(telegram_id)
    return User.objects.create_user(
        telegram_id=telegram_id,
        first_name='KYCSmoke',
        username=f'kyc_smoke_{telegram_id}',
    )


def make_test_pdf(name: str = 'test.pdf', size: int = 1024) -> SimpleUploadedFile:
    content = b'%PDF-1.4\n' + b'X' * (size - 9)
    return SimpleUploadedFile(name, content, content_type='application/pdf')


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_name_matching():
    section('TEST 1: Name matching helper')

    check(names_match('JOHN DOE', 'JOHN DOE'), 'exact match')
    check(names_match('john doe', 'JOHN DOE'), 'case insensitive')
    check(names_match('JOHN  DOE', 'JOHN DOE'), 'whitespace tolerance')
    check(
        names_match('JOHN ADEYEMI DOE', 'ADEYEMI JOHN DOE'),
        'name reordering matches via sorted-tokens',
    )
    check(not names_match('JOHN DOE', 'JANE DOE'), 'rejects different names')
    check(not names_match('', 'JOHN DOE'), 'rejects empty name')

    sim = name_similarity('JOHN ADEYEMI DOE', 'JOHN DOE')
    check(0.5 < sim < 0.9, f'partial overlap has sensible similarity ({sim:.2f})')


@override_settings(KYC_PROVIDER='stub')
def test_resolve_bank_endpoint():
    section('TEST 2: resolve_bank — live bank lookup')

    result = KYCService.resolve_bank('058', '1234567890')
    check(result['account_name'] == 'JOHN DOE', 'returns account name')
    check(result['bank_code'] == '058', 'echoes bank code')

    try:
        KYCService.resolve_bank('058', '1234569999')
        check(False, 'rejects account that does not exist')
    except KYCServiceError:
        check(True, 'rejects account that does not exist')

    try:
        KYCService.resolve_bank('058', '12345')
        check(False, 'rejects account number too short')
    except KYCServiceError:
        check(True, 'rejects account number too short')

    try:
        KYCService.resolve_bank('', '1234567890')
        check(False, 'rejects empty bank code')
    except KYCServiceError:
        check(True, 'rejects empty bank code')


@override_settings(KYC_PROVIDER='stub')
def test_document_upload():
    section('TEST 3: Document upload validation')

    user = make_test_user(800000020)

    # Valid PDF
    pdf = make_test_pdf('utility.pdf', 2048)
    doc = KYCService.upload_document(user, pdf, 'utility_bill')
    check(doc.id is not None, 'valid PDF uploaded')
    check(doc.document_type == 'utility_bill', 'document type stored')
    check(doc.is_active, 'newly uploaded doc is active')

    # File too large
    big_file = SimpleUploadedFile(
        'big.pdf',
        b'X' * (6 * 1024 * 1024),
        content_type='application/pdf',
    )
    try:
        KYCService.upload_document(user, big_file)
        check(False, 'rejects file over 5MB')
    except KYCServiceError as e:
        check('too large' in str(e).lower(), 'rejects file over 5MB')

    # Wrong content type
    exe_file = SimpleUploadedFile(
        'malware.exe',
        b'MZ\x90\x00' * 100,
        content_type='application/x-msdownload',
    )
    try:
        KYCService.upload_document(user, exe_file)
        check(False, 'rejects unsupported file type')
    except KYCServiceError:
        check(True, 'rejects unsupported file type')

    # Empty file
    empty = SimpleUploadedFile('empty.pdf', b'', content_type='application/pdf')
    try:
        KYCService.upload_document(user, empty)
        check(False, 'rejects empty file')
    except KYCServiceError as e:
        check('empty' in str(e).lower(), 'rejects empty file')

    cleanup(800000020)


@override_settings(KYC_PROVIDER='stub')
def test_full_submit_happy_path():
    section('TEST 4: Full KYC submit — all sections verified')

    user = make_test_user(800000021)
    doc = KYCService.upload_document(user, make_test_pdf())

    payload = {
        'full_name': 'JOHN DOE',
        'nin': '12345678901',
        'bvn': '12345678901',
        'date_of_birth': date(1990, 1, 1),
        'phone_number': '08012345678',
        'bank_code': '058',
        'account_number': '1234567890',
        'document_id': str(doc.id),
    }

    profile = KYCService.submit(user, payload)

    check(
        profile.personal_info_status == KYCProfile.SectionStatus.VERIFIED,
        'personal_info verified',
    )
    check(
        profile.bank_account_status == KYCProfile.SectionStatus.VERIFIED,
        'bank_account verified',
    )
    check(
        profile.document_status == KYCProfile.SectionStatus.VERIFIED,
        'document verified',
    )
    check(
        profile.overall_status == KYCProfile.OverallStatus.APPROVED,
        'overall status approved',
    )
    check(profile.can_withdraw, 'can_withdraw is True')

    bank = BankAccount.objects.filter(user=user, is_active=True).first()
    check(bank is not None, 'BankAccount record created')
    check(bank.account_name == 'JOHN DOE', 'bank account name stored')
    check(bank.verified_at is not None, 'verified_at set')

    cleanup(800000021)


@override_settings(KYC_PROVIDER='stub')
def test_nin_name_mismatch():
    section('TEST 5: NIN name mismatch — personal_info rejected, others can pass')

    user = make_test_user(800000022)
    doc = KYCService.upload_document(user, make_test_pdf())

    payload = {
        'full_name': 'JOHN DOE',
        'nin': '12345670000',          # ends 0000 → 'MISMATCH NAME'
        'bvn': '12345678901',
        'date_of_birth': date(1990, 1, 1),
        'phone_number': '08012345678',
        'bank_code': '058',
        'account_number': '1234567890',
        'document_id': str(doc.id),
    }

    profile = KYCService.submit(user, payload)

    check(
        profile.personal_info_status == KYCProfile.SectionStatus.REQUIRES_CORRECTION,
        'personal_info marked requires_correction',
    )
    check(
        'mismatch' in profile.personal_info_reason.lower(),
        'reason mentions mismatch',
    )
    check(
        profile.document_status == KYCProfile.SectionStatus.VERIFIED,
        'document still verified despite NIN issue',
    )
    check(
        profile.overall_status == KYCProfile.OverallStatus.PARTIAL,
        'overall status is partial',
    )
    check(not profile.can_withdraw, 'cannot withdraw with partial status')

    cleanup(800000022)


@override_settings(KYC_PROVIDER='stub')
def test_bvn_name_mismatch():
    section('TEST 6: BVN name mismatch')

    user = make_test_user(800000023)
    doc = KYCService.upload_document(user, make_test_pdf())

    payload = {
        'full_name': 'JOHN DOE',
        'nin': '12345678901',
        'bvn': '12345670000',          # ends 0000
        'date_of_birth': date(1990, 1, 1),
        'phone_number': '08012345678',
        'bank_code': '058',
        'account_number': '1234567890',
        'document_id': str(doc.id),
    }

    profile = KYCService.submit(user, payload)

    check(
        profile.personal_info_status == KYCProfile.SectionStatus.REQUIRES_CORRECTION,
        'personal_info rejected on BVN mismatch',
    )
    check(
        'bvn' in profile.personal_info_reason.lower() or
        'mismatch' in profile.personal_info_reason.lower(),
        'reason mentions BVN mismatch',
    )

    cleanup(800000023)


@override_settings(KYC_PROVIDER='stub')
def test_bank_account_name_mismatch():
    section('TEST 7: Bank account name mismatch')

    user = make_test_user(800000024)
    doc = KYCService.upload_document(user, make_test_pdf())

    payload = {
        'full_name': 'JOHN DOE',
        'nin': '12345678901',
        'bvn': '12345678901',
        'date_of_birth': date(1990, 1, 1),
        'phone_number': '08012345678',
        'bank_code': '058',
        'account_number': '1234560000', # ends 0000 → 'WRONG PERSON'
        'document_id': str(doc.id),
    }

    profile = KYCService.submit(user, payload)

    check(
        profile.personal_info_status == KYCProfile.SectionStatus.VERIFIED,
        'personal_info still verified',
    )
    check(
        profile.bank_account_status == KYCProfile.SectionStatus.REQUIRES_CORRECTION,
        'bank_account rejected',
    )
    check(
        'name' in profile.bank_account_reason.lower(),
        'reason mentions name mismatch',
    )
    check(
        not BankAccount.objects.filter(user=user, is_active=True).exists(),
        'no bank account record created on mismatch',
    )

    cleanup(800000024)


@override_settings(KYC_PROVIDER='stub')
def test_provider_error():
    section('TEST 8: Provider error — graceful failure')

    user = make_test_user(800000025)
    doc = KYCService.upload_document(user, make_test_pdf())

    payload = {
        'full_name': 'JOHN DOE',
        'nin': '12345679999',          # ends 9999 → provider error
        'bvn': '12345678901',
        'date_of_birth': date(1990, 1, 1),
        'phone_number': '08012345678',
        'bank_code': '058',
        'account_number': '1234567890',
        'document_id': str(doc.id),
    }

    profile = KYCService.submit(user, payload)

    check(
        profile.personal_info_status == KYCProfile.SectionStatus.REQUIRES_CORRECTION,
        'personal_info marked requires_correction on provider failure',
    )
    check(
        'failed' in profile.personal_info_reason.lower() or
        'lookup' in profile.personal_info_reason.lower(),
        'reason mentions lookup failure',
    )

    cleanup(800000025)


@override_settings(KYC_PROVIDER='stub')
def test_resubmission():
    section('TEST 9: Resubmission — fix one section, others stay verified')

    user = make_test_user(800000026)
    doc = KYCService.upload_document(user, make_test_pdf())

    bad_payload = {
        'full_name': 'JOHN DOE',
        'nin': '12345678901',
        'bvn': '12345678901',
        'date_of_birth': date(1990, 1, 1),
        'phone_number': '08012345678',
        'bank_code': '058',
        'account_number': '1234560000', # bad
        'document_id': str(doc.id),
    }
    profile = KYCService.submit(user, bad_payload)
    check(
        profile.personal_info_status == KYCProfile.SectionStatus.VERIFIED,
        'first submit: personal_info verified',
    )
    check(
        profile.bank_account_status == KYCProfile.SectionStatus.REQUIRES_CORRECTION,
        'first submit: bank rejected',
    )

    # Resubmit with fixed bank account
    good_payload = {**bad_payload, 'account_number': '1234567890'}
    profile = KYCService.submit(user, good_payload)
    check(
        profile.bank_account_status == KYCProfile.SectionStatus.VERIFIED,
        'resubmit: bank now verified',
    )
    check(
        profile.overall_status == KYCProfile.OverallStatus.APPROVED,
        'resubmit: overall now approved',
    )
    check(
        profile.last_resubmission_at is not None,
        'last_resubmission_at recorded',
    )

    cleanup(800000026)


@override_settings(KYC_PROVIDER='stub')
def test_bank_account_replacement():
    section('TEST 10: Bank account replacement — old deactivated, new active')

    user = make_test_user(800000027)
    doc = KYCService.upload_document(user, make_test_pdf())

    payload = {
        'full_name': 'JOHN DOE',
        'nin': '12345678901',
        'bvn': '12345678901',
        'date_of_birth': date(1990, 1, 1),
        'phone_number': '08012345678',
        'bank_code': '058',
        'account_number': '1234567890',
        'document_id': str(doc.id),
    }
    KYCService.submit(user, payload)
    first_bank = BankAccount.objects.filter(user=user, is_active=True).first()
    check(first_bank is not None, 'first bank account active')
    check(first_bank.account_number == '1234567890', 'first account number stored')

    # Submit again with different account number
    payload['account_number'] = '9876543210'
    KYCService.submit(user, payload)

    first_bank.refresh_from_db()
    check(not first_bank.is_active, 'old bank account deactivated')

    new_bank = BankAccount.objects.filter(user=user, is_active=True).first()
    check(new_bank is not None, 'new bank account active')
    check(new_bank.account_number == '9876543210', 'new account number stored')
    check(
        BankAccount.objects.filter(user=user).count() == 2,
        'history preserved (2 records)',
    )
    check(
        BankAccount.objects.filter(user=user, is_active=True).count() == 1,
        'only one active bank account at a time',
    )

    cleanup(800000027)


@override_settings(KYC_PROVIDER='stub')
def test_validation_errors():
    section('TEST 11: Input validation — invalid data rejected')

    user = make_test_user(800000028)
    doc = KYCService.upload_document(user, make_test_pdf())

    payload = {
        'full_name': 'JOHN DOE',
        'nin': '12345',                # too short
        'bvn': '12345678901',
        'date_of_birth': date(1990, 1, 1),
        'phone_number': '08012345678',
        'bank_code': '058',
        'account_number': '1234567890',
        'document_id': str(doc.id),
    }
    profile = KYCService.submit(user, payload)
    check(
        profile.personal_info_status == KYCProfile.SectionStatus.REQUIRES_CORRECTION,
        'rejects NIN that is too short',
    )
    check('nin' in profile.personal_info_reason.lower(), 'reason mentions NIN')

    payload2 = {**payload, 'nin': '12345678901', 'bvn': ''}
    profile = KYCService.submit(user, payload2)
    check(
        profile.personal_info_status == KYCProfile.SectionStatus.REQUIRES_CORRECTION,
        'rejects empty BVN',
    )

    cleanup(800000028)


@override_settings(KYC_PROVIDER='stub')
def test_status_endpoint():
    section('TEST 12: get_status — returns proper snapshot')

    user = make_test_user(800000029)

    status_data = KYCService.get_status(user)
    check(
        status_data['overall_status'] == KYCProfile.OverallStatus.UNVERIFIED,
        'unverified before submission',
    )
    check(not status_data['can_withdraw'], 'cannot withdraw before submission')

    doc = KYCService.upload_document(user, make_test_pdf())
    payload = {
        'full_name': 'JOHN DOE',
        'nin': '12345678901',
        'bvn': '12345678901',
        'date_of_birth': date(1990, 1, 1),
        'phone_number': '08012345678',
        'bank_code': '058',
        'account_number': '1234567890',
        'document_id': str(doc.id),
    }
    KYCService.submit(user, payload)

    status_data = KYCService.get_status(user)
    check(
        status_data['overall_status'] == KYCProfile.OverallStatus.APPROVED,
        'approved after happy-path submit',
    )
    check(status_data['can_withdraw'], 'can withdraw after approval')
    check(status_data['submitted_at'] is not None, 'submitted_at populated')

    cleanup(800000029)


@override_settings(KYC_PROVIDER='stub')
def test_encryption():
    section('TEST 13: PII encryption at rest')

    user = make_test_user(800000030)
    doc = KYCService.upload_document(user, make_test_pdf())

    payload = {
        'full_name': 'JOHN DOE',
        'nin': '12345678901',
        'bvn': '98765432109',
        'date_of_birth': date(1990, 1, 1),
        'phone_number': '08012345678',
        'bank_code': '058',
        'account_number': '1234567890',
        'document_id': str(doc.id),
    }
    KYCService.submit(user, payload)

    # Read raw from DB to confirm encryption
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT nin, bvn FROM kyc_profiles WHERE user_id = %s',
            [user.id],
        )
        row = cursor.fetchone()

    raw_nin, raw_bvn = row
    check(raw_nin != '12345678901', 'NIN is NOT stored as plaintext')
    check(raw_bvn != '98765432109', 'BVN is NOT stored as plaintext')

    # But the model returns decrypted values
    profile = KYCProfile.objects.get(user=user)
    check(profile.nin == '12345678901', 'NIN decrypts on read')
    check(profile.bvn == '98765432109', 'BVN decrypts on read')

    cleanup(800000030)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print('SPIN REWARDS — KYC SMOKE TEST')
    print('Testing the KYC service with stub provider.\n')

    try:
        test_name_matching()
        test_resolve_bank_endpoint()
        test_document_upload()
        test_full_submit_happy_path()
        test_nin_name_mismatch()
        test_bvn_name_mismatch()
        test_bank_account_name_mismatch()
        test_provider_error()
        test_resubmission()
        test_bank_account_replacement()
        test_validation_errors()
        test_status_endpoint()
        test_encryption()
    finally:
        for tid in range(800000020, 800000031):
            cleanup(tid)

    print(f'\n{"=" * 60}')
    print(f'RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print(f'{"=" * 60}')

    if TESTS_FAILED:
        print('\n❌ KYC SMOKE TEST FAILED')
        sys.exit(1)
    else:
        print('\n✅ KYC SMOKE TEST PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()