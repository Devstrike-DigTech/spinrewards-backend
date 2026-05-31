"""
KYC + Withdrawals (redesign) smoke test.

Tests:
  1. KYC submit (NIN-only) → user verified, nin_full_name stored
  2. KYC submit twice → second attempt rejected with NIN_ALREADY_VERIFIED
  3. KYC status returns new shape
  4. Resolve-bank deprecated endpoint returns 410
  5. Add saved account: name matches → saved
  6. Add saved account: name mismatch → 400 with bank name only (no NIN name leak)
  7. List saved accounts
  8. Set another account as default → previous default flipped
  9. Soft-delete account → not visible in list, withdrawal history preserved
  10. Name match algorithm: edge cases

Usage:
    docker compose exec api python smoke_test_kyc_redesign.py
"""
import os
import sys
from decimal import Decimal
from unittest.mock import MagicMock, patch

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


from apps.kyc.models import BankAccount, KYCProfile
from apps.kyc.name_match import names_match, names_match_with_score
from apps.users.models import User


SMOKE_TELEGRAM_IDS = [800001001, 800001002]


def cleanup():
    for tid in SMOKE_TELEGRAM_IDS:
        try:
            u = User.objects.get(telegram_id=tid)
            BankAccount.objects.filter(user=u).delete()
            KYCProfile.objects.filter(user=u).delete()
            u.delete()
        except User.DoesNotExist:
            pass


def make_user(tid, first_name='Smoke', last_name='Tester'):
    return User.objects.create(
        telegram_id=tid,
        first_name=first_name,
        last_name=last_name,
        username=f'smoke_{tid}',
    )


# ─── Mocked provider helpers ──────────────────────────────────────────────────

def mock_nin_lookup(full_name='UZOR RICHARD CHUKWUEMEKA', dob='1995-01-15'):
    """Build a mock NIN lookup result object."""
    m = MagicMock()
    m.full_name = full_name
    m.date_of_birth = dob
    m.raw_response = {'fullName': full_name, 'dob': dob}
    return m


def mock_bank_resolve(account_name, bank_name='GTBank'):
    """Build a mock bank resolution result object."""
    m = MagicMock()
    m.account_name = account_name
    m.bank_name = bank_name
    return m


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_name_match_algorithm():
    section('TEST 1: Name match algorithm — edge cases')

    cases = [
        # (nin_name, bank_name, expected_match, label)
        ('UZOR RICHARD CHUKWUEMEKA', 'Richard Uzor', True, 'order + missing middle'),
        ('UZOR RICHARD CHUKWUEMEKA', 'UZOR RICHARD', True, 'missing middle name'),
        ('UZOR RICHARD CHUKWUEMEKA', 'uzor richard chukwuemeka', True, 'case insensitive'),
        ('ADEYEMI JOHN', 'JOHN ADEYEMI', True, 'reordered, two-token'),
        ('Mr. UZOR RICHARD', 'Richard Uzor', True, 'title stripped'),
        ('UZOR RICHARD', 'ADEBAYO RICHARD', False, 'different surname'),
        ('UZOR', 'UZOR RICHARD', False, 'single-token NIN too weak'),
        ('UZOR RICHARD', 'UZOR R', False, 'initial vs full'),
        ('', 'UZOR RICHARD', False, 'empty NIN name'),
        ('UZOR RICHARD CHUKWUEMEKA', '', False, 'empty bank name'),
    ]

    for nin_name, bank_name, expected, label in cases:
        actual = names_match(nin_name, bank_name)
        check(
            actual == expected,
            f'{label}: "{nin_name}" vs "{bank_name}" → {"match" if expected else "no-match"}',
        )


def test_kyc_submit_nin_only(client, user):
    section('TEST 2: KYC submit — NIN only, name + DOB stored')

    nin_result = mock_nin_lookup(full_name='UZOR RICHARD CHUKWUEMEKA', dob='1995-01-15')

    with patch('apps.kyc.services.get_provider') as mock_get:
        mock_provider = MagicMock()
        mock_provider.lookup_nin.return_value = nin_result
        mock_get.return_value = mock_provider

        res = client.post('/api/v1/kyc/submit/', {
            'full_name': 'Richard Uzor',   # case different, missing middle name → matches via fuzzy
            'nin': '12345678901',
            'date_of_birth': '1995-01-15',
            'phone_number': '+2348012345678',
        }, format='json')

    check(res.status_code == 200, f'submit returns 200 (got {res.status_code})')
    if res.status_code == 200:
        data = res.data['data']
        check(data['nin_verified'] is True, 'nin_verified=True')
        check(data['can_withdraw'] is True, 'can_withdraw=True')
        check(data['nin_full_name'] == 'UZOR RICHARD CHUKWUEMEKA',
              'nin_full_name stored from provider')


def test_kyc_submit_locked_after_verified(client, user):
    section('TEST 3: KYC submit — locked after NIN verification')

    nin_result = mock_nin_lookup()

    with patch('apps.kyc.services.get_provider') as mock_get:
        mock_provider = MagicMock()
        mock_provider.lookup_nin.return_value = nin_result
        mock_get.return_value = mock_provider

        res = client.post('/api/v1/kyc/submit/', {
            'full_name': 'Richard Uzor',
            'nin': '99999999999',
            'date_of_birth': '1995-01-15',
        }, format='json')

    check(res.status_code == 400, f'second submit blocked (got {res.status_code})')
    check(res.data.get('code') == 'NIN_ALREADY_VERIFIED', 'NIN_ALREADY_VERIFIED error code')


def test_kyc_status_shape(client):
    section('TEST 4: KYC status returns new shape')

    res = client.get('/api/v1/kyc/status/')
    check(res.status_code == 200, 'status returns 200')

    data = res.data['data']
    for expected_field in ('overall_status', 'nin_verified', 'nin_full_name',
                           'personal_info_status', 'document_status',
                           'can_withdraw'):
        check(expected_field in data, f'response has "{expected_field}"')

    # Should NOT have BVN fields
    check('bvn_verified' not in data, 'response does NOT have "bvn_verified"')
    check('bank_account_status' not in data, 'response does NOT have "bank_account_status"')


def test_resolve_bank_deprecated(client):
    section('TEST 5: Old resolve-bank endpoint returns 410 Gone')

    res = client.post('/api/v1/kyc/resolve-bank/', {
        'bank_code': '058',
        'account_number': '0123456789',
    }, format='json')
    check(res.status_code == 410, f'resolve-bank → 410 (got {res.status_code})')
    check(res.data.get('code') == 'ENDPOINT_DEPRECATED', 'ENDPOINT_DEPRECATED code')


def test_add_saved_account_name_match_success(client, user):
    section('TEST 6: Add saved account — name match passes')

    # The user's nin_full_name is "UZOR RICHARD CHUKWUEMEKA"
    bank_result = mock_bank_resolve(account_name='RICHARD UZOR', bank_name='GTBank')

    with patch('apps.withdrawals.bank_account_service.get_provider') as mock_get:
        mock_provider = MagicMock()
        mock_provider.resolve_bank_account.return_value = bank_result
        mock_get.return_value = mock_provider

        res = client.post('/api/v1/withdrawals/saved-accounts/', {
            'bank_code': '058',
            'account_number': '0123456789',
        }, format='json')

    check(res.status_code == 201, f'saved-accounts POST → 201 (got {res.status_code})')
    if res.status_code == 201:
        data = res.data['data']
        check(data['account_name'] == 'RICHARD UZOR', 'account_name stored from provider')
        check(data['is_default'] is True, 'first account auto-set as default')

        # DB check
        from apps.kyc.models import BankAccount
        acct = BankAccount.objects.filter(user=user, is_active=True).first()
        check(acct is not None, 'account persisted to DB')
        check(acct.verified_at is not None, 'verified_at timestamp set')


def test_add_saved_account_name_mismatch(client, user):
    section('TEST 7: Add saved account — name mismatch → 400, no NIN name leaked')

    bank_result = mock_bank_resolve(account_name='ADEBAYO JOHN', bank_name='Access Bank')

    with patch('apps.withdrawals.bank_account_service.get_provider') as mock_get:
        mock_provider = MagicMock()
        mock_provider.resolve_bank_account.return_value = bank_result
        mock_get.return_value = mock_provider

        res = client.post('/api/v1/withdrawals/saved-accounts/', {
            'bank_code': '044',
            'account_number': '9876543210',
        }, format='json')

    check(res.status_code == 400, f'name mismatch → 400 (got {res.status_code})')
    check(res.data.get('code') == 'NAME_MISMATCH', 'NAME_MISMATCH code')
    check(res.data.get('account_name') == 'ADEBAYO JOHN', 'bank-returned name in response')
    check('kyc_name' not in res.data, 'NIN name NOT leaked in response')
    check('nin_full_name' not in res.data, 'nin_full_name NOT leaked')


def test_list_saved_accounts(client):
    section('TEST 8: List saved accounts')

    res = client.get('/api/v1/withdrawals/saved-accounts/')
    check(res.status_code == 200, 'list → 200')
    accounts = res.data['data']['accounts']
    check(len(accounts) == 1, f'should have 1 saved account (got {len(accounts)})')
    if accounts:
        check(accounts[0]['is_default'] is True, 'default flag present')
        masked = accounts[0]['account_number_masked']
        check(masked.startswith('****'), f'account number masked: {masked}')


def test_add_second_account_and_default_swap(client, user):
    section('TEST 9: Add second account + default switching')

    # Add a second matching account
    bank_result = mock_bank_resolve(account_name='UZOR RICHARD', bank_name='Zenith Bank')
    with patch('apps.withdrawals.bank_account_service.get_provider') as mock_get:
        mock_provider = MagicMock()
        mock_provider.resolve_bank_account.return_value = bank_result
        mock_get.return_value = mock_provider

        res = client.post('/api/v1/withdrawals/saved-accounts/', {
            'bank_code': '057',
            'account_number': '5555666677',
        }, format='json')

    check(res.status_code == 201, 'second account added')

    # Newest should now be default
    from apps.kyc.models import BankAccount
    accts = list(BankAccount.objects.filter(user=user, is_active=True))
    defaults = [a for a in accts if a.is_default]
    check(len(defaults) == 1, f'exactly one default (got {len(defaults)})')
    check(len(accts) == 2, 'two active accounts')

    # Manually set the FIRST one as default again
    first_acct = next(a for a in accts if a.account_number == '0123456789')
    res = client.post(f'/api/v1/withdrawals/saved-accounts/{first_acct.id}/set-default/')
    check(res.status_code == 200, 'set-default returns 200')

    first_acct.refresh_from_db()
    check(first_acct.is_default is True, 'first account is now default')

    other = BankAccount.objects.get(account_number='5555666677')
    check(other.is_default is False, 'other account no longer default')


def test_soft_delete_account(client, user):
    section('TEST 10: Soft-delete an account')

    from apps.kyc.models import BankAccount
    other = BankAccount.objects.get(account_number='5555666677')
    other_id = other.id

    res = client.delete(f'/api/v1/withdrawals/saved-accounts/{other_id}/')
    check(res.status_code == 200, 'delete returns 200')

    other.refresh_from_db()
    check(other.is_active is False, 'is_active set to False (soft delete)')

    # Re-list — should only show 1
    res = client.get('/api/v1/withdrawals/saved-accounts/')
    check(len(res.data['data']['accounts']) == 1, 'list now shows only 1 account')


def test_kyc_required_for_account_creation():
    section('TEST 11: KYC_REQUIRED if user has no NIN verified')

    # Create a brand new user with no KYC
    user2 = make_user(SMOKE_TELEGRAM_IDS[1])
    client = APIClient()
    client.force_authenticate(user=user2)

    res = client.post('/api/v1/withdrawals/saved-accounts/', {
        'bank_code': '058',
        'account_number': '0000000000',
    }, format='json')

    check(res.status_code == 403, f'no KYC → 403 (got {res.status_code})')
    check(res.data.get('code') == 'KYC_REQUIRED', 'KYC_REQUIRED code')


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print('SPIN REWARDS — KYC + WITHDRAWALS REDESIGN SMOKE TEST\n')

    cleanup()

    # Test 1 doesn't need user setup
    test_name_match_algorithm()

    # Tests 2-10 use user1 with a verified KYC
    user1 = make_user(SMOKE_TELEGRAM_IDS[0], first_name='Richard', last_name='Uzor')
    client = APIClient()
    client.force_authenticate(user=user1)

    try:
        test_kyc_submit_nin_only(client, user1)
        test_kyc_submit_locked_after_verified(client, user1)
        test_kyc_status_shape(client)
        test_resolve_bank_deprecated(client)
        test_add_saved_account_name_match_success(client, user1)
        test_add_saved_account_name_mismatch(client, user1)
        test_list_saved_accounts(client)
        test_add_second_account_and_default_swap(client, user1)
        test_soft_delete_account(client, user1)
        test_kyc_required_for_account_creation()
    finally:
        cleanup()

    print(f'\n{"=" * 60}')
    print(f'RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print(f'{"=" * 60}')

    if TESTS_FAILED:
        print('\n❌ KYC REDESIGN SMOKE TEST FAILED')
        sys.exit(1)
    else:
        print('\n✅ KYC REDESIGN SMOKE TEST PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()