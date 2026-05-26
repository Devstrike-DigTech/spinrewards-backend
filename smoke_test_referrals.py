"""
Referrals module smoke test.

Tests:
  1. Service — code generation (unique, idempotent)
  2. Service — apply_code with validations (self-referral, duplicate, deposited-already)
  3. Service — qualify after first deposit (fires challenge signal)
  4. Signals — first deposit auto-qualifies referral
  5. Player endpoints — my-code, apply, my-referrals
  6. Admin endpoint — list with filters
  7. End-to-end — code → apply → first deposit → reward

Usage:
    docker compose exec api python smoke_test_referrals.py
"""
import os
import sys
from decimal import Decimal

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


from apps.challenges.models import Challenge, ChallengeProgress
from apps.referrals.models import Referral, ReferralCode
from apps.referrals.services import ReferralService, ReferralServiceError
from apps.users.models import User
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService


def cleanup():
    for tid in [600001001, 600001002, 600001003, 600001099]:
        try:
            u = User.objects.get(telegram_id=tid)
            Referral.objects.filter(referrer=u).delete()
            Referral.objects.filter(referred_user=u).delete()
            ReferralCode.objects.filter(user=u).delete()
            ChallengeProgress.objects.filter(user=u).delete()
            Transaction.objects.filter(user=u).delete()
            u.delete()
        except User.DoesNotExist:
            pass
    Challenge.objects.filter(name__startswith='SMOKE_REF_').delete()


def create_test_data():
    cleanup()

    admin = User.objects.create_user(
        telegram_id=600001099,
        first_name='RefAdmin',
        username='ref_admin',
    )
    admin.is_staff = True
    admin.save()

    referrer = User.objects.create_user(
        telegram_id=600001001,
        first_name='Referrer',
        last_name='User',
        username='referrer',
    )

    referred1 = User.objects.create_user(
        telegram_id=600001002,
        first_name='Referred',
        last_name='One',
        username='referred_1',
    )

    referred2 = User.objects.create_user(
        telegram_id=600001003,
        first_name='Referred',
        last_name='Two',
        username='referred_2',
    )

    # Create a referral challenge so qualify() distributes rewards
    Challenge.objects.create(
        name='SMOKE_REF_BringFriend',
        type=Challenge.Type.REFERRAL,
        recurrence=Challenge.Recurrence.PERMANENT,
        criteria={'action': 'referral', 'target_count': 1},
        reward={'type': 'cash', 'amount': 1000},
        is_active=True,
        is_visible=True,
    )

    return admin, referrer, referred1, referred2


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_code_generation(referrer):
    section('TEST 1: Code generation')

    code = ReferralService.get_or_create_code(referrer)
    check(code is not None, 'code created')
    check(code.code.startswith('SPIN-'), f'code has SPIN- prefix: {code.code}')
    check(len(code.code) == 13, f'code is 13 chars total: {code.code} ({len(code.code)})')

    # Idempotency
    code2 = ReferralService.get_or_create_code(referrer)
    check(code.code == code2.code, 'same code returned on second call (idempotent)')


def test_apply_code(referrer, referred1):
    section('TEST 2: Apply referral code')

    code = ReferralService.get_or_create_code(referrer)

    # Apply valid code
    referral = ReferralService.apply_code(referred1, code.code)
    check(referral is not None, 'referral created')
    check(referral.referrer == referrer, 'referrer set correctly')
    check(referral.referred_user == referred1, 'referred_user set correctly')
    check(referral.status == Referral.Status.PENDING, 'status is pending initially')


def test_self_referral_blocked(referrer):
    section('TEST 3: Self-referral blocked')

    code = ReferralService.get_or_create_code(referrer)

    try:
        ReferralService.apply_code(referrer, code.code)
        check(False, 'self-referral should have raised error')
    except ReferralServiceError as e:
        check('own referral code' in str(e).lower(), f'correct error: {e}')


def test_duplicate_apply_blocked(referrer, referred1):
    section('TEST 4: Duplicate apply blocked')

    code = ReferralService.get_or_create_code(referrer)

    # referred1 already has a referral from test 2 — try to apply again
    try:
        ReferralService.apply_code(referred1, code.code)
        check(False, 'duplicate should have raised error')
    except ReferralServiceError as e:
        check('already used' in str(e).lower(), f'correct error: {e}')


def test_invalid_code_blocked(referred2):
    section('TEST 5: Invalid code blocked')

    try:
        ReferralService.apply_code(referred2, 'SPIN-INVALID9')
        check(False, 'invalid code should have raised error')
    except ReferralServiceError as e:
        check('invalid or inactive' in str(e).lower(), f'correct error: {e}')


def test_qualify_after_deposit(referrer, referred1):
    section('TEST 6: Qualify after first deposit')

    # Track referrer's initial cash (should get reward via challenge)
    initial_cash = WalletService.get_balance(referrer, 'cash')

    # referred1 has a pending referral from test 2
    # Make their first deposit — should trigger qualify automatically via signal
    WalletService.credit(
        user=referred1,
        amount=Decimal('500'),
        balance_type='coin',
        tx_type=Transaction.Type.DEPOSIT,
        reference_id='smoke_ref_first_deposit',
        metadata={'source': 'smoke_test'},
    )

    # Refresh the referral
    referral = Referral.objects.get(referred_user=referred1)
    check(
        referral.status in (Referral.Status.QUALIFIED, Referral.Status.REWARDED),
        f'referral qualified after first deposit (status={referral.status})',
    )
    check(referral.qualified_at is not None, 'qualified_at recorded')

    # Check the referrer got their reward
    final_cash = WalletService.get_balance(referrer, 'cash')
    check(
        final_cash == initial_cash + Decimal('1000'),
        f'referrer got 1000 reward (before={initial_cash}, after={final_cash})',
    )

    # Verify challenge progress was incremented
    challenge = Challenge.objects.get(name='SMOKE_REF_BringFriend')
    progress = ChallengeProgress.objects.filter(
        user=referrer, challenge=challenge,
    ).first()
    check(progress is not None, 'challenge progress created for referrer')
    check(progress.is_completed, 'challenge marked completed')


def test_second_deposit_no_requalify(referred1):
    section('TEST 7: Second deposit does not re-qualify')

    # Referred1 already qualified on first deposit
    # Second deposit should not change anything
    referral_before = Referral.objects.get(referred_user=referred1)
    qualified_at_before = referral_before.qualified_at

    WalletService.credit(
        user=referred1,
        amount=Decimal('500'),
        balance_type='coin',
        tx_type=Transaction.Type.DEPOSIT,
        reference_id='smoke_ref_second_deposit',
    )

    referral_after = Referral.objects.get(referred_user=referred1)
    check(
        referral_after.qualified_at == qualified_at_before,
        'qualified_at unchanged after second deposit',
    )


def test_referrals_stats(referrer):
    section('TEST 8: Referral stats')

    stats = ReferralService.get_referral_stats(referrer)
    check('total_referrals' in stats, 'stats has total_referrals')
    check('pending' in stats, 'stats has pending')
    check('qualified' in stats, 'stats has qualified')
    check('rewarded' in stats, 'stats has rewarded')
    check(stats['total_referrals'] >= 1, 'at least 1 referral counted')


def test_player_my_code_endpoint(referrer):
    section('TEST 9: Player — GET /referrals/my-code/')

    client = APIClient()
    client.force_authenticate(user=referrer)

    res = client.get('/api/v1/referrals/my-code/')
    check(res.status_code == 200, 'returns 200')
    check(res.data['success'], 'success True')

    d = res.data['data']
    check('code' in d, 'code in response')
    check('share_url' in d, 'share_url in response')
    check('stats' in d, 'stats in response')
    check(d['code'].startswith('SPIN-'), 'code has correct prefix')


def test_player_apply_endpoint(referrer, referred2):
    section('TEST 10: Player — POST /referrals/apply/')

    client = APIClient()
    client.force_authenticate(user=referred2)

    referrer_code = ReferralService.get_or_create_code(referrer).code

    # Apply valid
    res = client.post(
        '/api/v1/referrals/apply/',
        {'code': referrer_code}, format='json',
    )
    check(res.status_code == 200, 'apply returns 200')
    check(res.data['success'], 'success True')
    check('referral_id' in res.data['data'], 'referral_id in response')

    # Missing code
    res_bad = client.post('/api/v1/referrals/apply/', {}, format='json')
    check(res_bad.status_code == 400, 'missing code returns 400')
    check(res_bad.data['code'] == 'MISSING_CODE', 'MISSING_CODE error')

    # Invalid code
    res_inv = client.post(
        '/api/v1/referrals/apply/', {'code': 'SPIN-NOPE9999'}, format='json',
    )
    check(res_inv.status_code == 400, 'invalid code returns 400')


def test_player_my_referrals_endpoint(referrer):
    section('TEST 11: Player — GET /referrals/my-referrals/')

    client = APIClient()
    client.force_authenticate(user=referrer)

    res = client.get('/api/v1/referrals/my-referrals/')
    check(res.status_code == 200, 'returns 200')

    d = res.data['data']
    check('stats' in d, 'stats in response')
    check('referrals' in d, 'referrals list in response')
    check(len(d['referrals']) >= 1, 'at least 1 referral in list')

    # Check referral shape
    if d['referrals']:
        ref = d['referrals'][0]
        check('referred_user' in ref, 'referral has referred_user')
        check('status' in ref, 'referral has status')


def test_admin_referrals_endpoint(admin):
    section('TEST 12: Admin — GET /admin/referrals/')

    client = APIClient()
    client.force_authenticate(user=admin)

    res = client.get('/api/v1/admin/referrals/')
    check(res.status_code == 200, 'admin list returns 200')

    d = res.data['data']
    check('overview' in d, 'overview in response')
    check('results' in d, 'results in response')

    overview = d['overview']
    for field in ('total', 'pending', 'qualified', 'rewarded'):
        check(field in overview, f'overview has {field}')

    # Filter by status
    res_filter = client.get('/api/v1/admin/referrals/?status=rewarded')
    check(res_filter.status_code == 200, 'status filter works')


def test_non_staff_blocked(referrer):
    section('TEST 13: Non-staff blocked from admin')

    client = APIClient()
    client.force_authenticate(user=referrer)

    res = client.get('/api/v1/admin/referrals/')
    check(res.status_code == 403, 'non-staff gets 403')


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print('SPIN REWARDS — REFERRALS SMOKE TEST')
    print('Testing service, signals, and all endpoints.\n')

    print('→ Creating test data...')
    admin, referrer, referred1, referred2 = create_test_data()
    print(f'  Admin:    {admin.telegram_id}')
    print(f'  Referrer: {referrer.telegram_id}')
    print(f'  Referred1: {referred1.telegram_id}')
    print(f'  Referred2: {referred2.telegram_id}')

    try:
        test_code_generation(referrer)
        test_apply_code(referrer, referred1)
        test_self_referral_blocked(referrer)
        test_duplicate_apply_blocked(referrer, referred1)
        test_invalid_code_blocked(referred2)
        test_qualify_after_deposit(referrer, referred1)
        test_second_deposit_no_requalify(referred1)
        test_referrals_stats(referrer)
        test_player_my_code_endpoint(referrer)
        test_player_apply_endpoint(referrer, referred2)
        test_player_my_referrals_endpoint(referrer)
        test_admin_referrals_endpoint(admin)
        test_non_staff_blocked(referrer)

    finally:
        print('\n→ Cleaning up test data...')
        cleanup()
        print('  Done.')

    print(f'\n{"=" * 60}')
    print(f'RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print(f'{"=" * 60}')

    if TESTS_FAILED:
        print('\n❌ REFERRALS SMOKE TEST FAILED')
        sys.exit(1)
    else:
        print('\n✅ REFERRALS SMOKE TEST PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()