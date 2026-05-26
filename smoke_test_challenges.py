"""
Challenges module smoke test.

Tests:
  1. Engine — window calculation for each recurrence type
  2. Engine — eligibility checks (welcome, one-time, min_stake, min_deposit)
  3. Engine — progress increment on spin/deposit/login/referral events
  4. Engine — reward distribution (coins, cash)
  5. Player endpoints — list + detail
  6. Admin endpoints — CRUD + participants + completions
  7. End-to-end — spin challenge through completion

Usage:
    docker compose exec api python smoke_test_challenges.py
"""
import os
import sys
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.utils import timezone
from rest_framework.test import APIClient

# ─── Test infrastructure ──────────────────────────────────────────────────────

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


from apps.challenges.engine import ChallengeEngine
from apps.challenges.models import Challenge, ChallengeProgress
from apps.spin.models import Spin, Wheel, WheelSegment
from apps.users.models import User
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService


def cleanup():
    """Remove all smoke test data."""
    for tid in [700001001, 700001002, 700001099]:
        try:
            u = User.objects.get(telegram_id=tid)
            ChallengeProgress.objects.filter(user=u).delete()
            Spin.objects.filter(user=u).delete()
            Transaction.objects.filter(user=u).delete()
            u.delete()
        except User.DoesNotExist:
            pass
    Challenge.objects.filter(name__startswith='SMOKE_CHALL_').delete()
    Wheel.objects.filter(name__startswith='SMOKE_CHALL_').delete()


def create_test_data():
    cleanup()

    # Admin
    admin = User.objects.create_user(
        telegram_id=700001099,
        first_name='ChallAdmin',
        username='chall_admin',
    )
    admin.is_staff = True
    admin.save()

    # Player 1 (will run challenges)
    user1 = User.objects.create_user(
        telegram_id=700001001,
        first_name='ChallPlayer',
        last_name='One',
        username='chall_player_1',
    )

    # Player 2 (for testing other paths)
    user2 = User.objects.create_user(
        telegram_id=700001002,
        first_name='ChallPlayer',
        last_name='Two',
        username='chall_player_2',
    )

    # Inactive wheel for spin tests (avoid overlap with prod wheels)
    wheel = Wheel.objects.create(
        name='SMOKE_CHALL_Wheel',
        wheel_type=Wheel.WheelType.STANDARD,
        currency_type=Wheel.CurrencyType.COIN,
        min_stake=Decimal('100'),
        max_stake=Decimal('1000'),
        rtp_target=Decimal('80'),
        is_active=False,
    )
    WheelSegment.objects.create(
        wheel=wheel, position=0, label='Loss',
        multiplier=Decimal('0'), probability_weight=50, color='#3a3a3a',
    )
    WheelSegment.objects.create(
        wheel=wheel, position=1, label='2x',
        multiplier=Decimal('2'), probability_weight=50, color='#1A237E',
    )

    return admin, user1, user2, wheel


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_window_calculation():
    section('TEST 1: Engine — window calculation')

    daily_challenge = Challenge.objects.create(
        name='SMOKE_CHALL_Daily',
        type=Challenge.Type.SPIN_COUNT,
        recurrence=Challenge.Recurrence.DAILY,
        criteria={'action': 'spin', 'target_count': 3},
        reward={'type': 'coins', 'amount': 100},
    )
    ws, we = ChallengeEngine._get_window(daily_challenge)
    today_midnight = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    check(ws == today_midnight, 'daily window starts at today midnight')
    check(we > ws, 'daily window end is after start')

    weekly_challenge = Challenge.objects.create(
        name='SMOKE_CHALL_Weekly',
        type=Challenge.Type.SPIN_COUNT,
        recurrence=Challenge.Recurrence.WEEKLY,
        criteria={'action': 'spin', 'target_count': 10},
        reward={'type': 'coins', 'amount': 500},
    )
    ws, we = ChallengeEngine._get_window(weekly_challenge)
    check(ws.weekday() == 0, 'weekly window starts on Monday')

    monthly_challenge = Challenge.objects.create(
        name='SMOKE_CHALL_Monthly',
        type=Challenge.Type.SPIN_COUNT,
        recurrence=Challenge.Recurrence.MONTHLY,
        criteria={'action': 'spin', 'target_count': 30},
        reward={'type': 'coins', 'amount': 2000},
    )
    ws, we = ChallengeEngine._get_window(monthly_challenge)
    check(ws.day == 1, 'monthly window starts on the 1st')

    one_time_challenge = Challenge.objects.create(
        name='SMOKE_CHALL_OneTime',
        type=Challenge.Type.WELCOME,
        recurrence=Challenge.Recurrence.ONE_TIME,
        criteria={'action': 'spin', 'target_count': 1},
        reward={'type': 'free_spins', 'amount': 1},
    )
    ws, we = ChallengeEngine._get_window(one_time_challenge)
    check(ws == one_time_challenge.starts_at, 'one_time window uses challenge starts_at')


def test_spin_count_challenge(user1, wheel):
    section('TEST 2: spin_count challenge — progress + completion + reward')

    challenge = Challenge.objects.create(
        name='SMOKE_CHALL_SpinFive',
        type=Challenge.Type.SPIN_COUNT,
        recurrence=Challenge.Recurrence.DAILY,
        criteria={'action': 'spin', 'target_count': 3},
        reward={'type': 'coins', 'amount': 150},
    )

    # Track initial coin balance
    initial_coins = WalletService.get_balance(user1, 'coin')

    seg = WheelSegment.objects.filter(wheel=wheel, position=0).first()

    # Spin 1
    Spin.objects.create(
        user=user1, wheel=wheel, stake_amount=Decimal('200'),
        payout_amount=Decimal('0'), outcome='loss',
        segment_landed=seg, reference='chall_spin_1',
        server_seed_hash='aaa', nonce=1,
    )

    progress = ChallengeProgress.objects.filter(user=user1, challenge=challenge).first()
    check(progress is not None, 'progress record created on first spin')
    check(progress.current_count == 1, 'count is 1 after first spin')
    check(progress.is_completed is False, 'not yet completed')

    # Spin 2
    Spin.objects.create(
        user=user1, wheel=wheel, stake_amount=Decimal('200'),
        payout_amount=Decimal('0'), outcome='loss',
        segment_landed=seg, reference='chall_spin_2',
        server_seed_hash='bbb', nonce=2,
    )
    progress.refresh_from_db()
    check(progress.current_count == 2, 'count is 2 after second spin')

    # Spin 3 — should trigger completion
    Spin.objects.create(
        user=user1, wheel=wheel, stake_amount=Decimal('200'),
        payout_amount=Decimal('0'), outcome='loss',
        segment_landed=seg, reference='chall_spin_3',
        server_seed_hash='ccc', nonce=3,
    )
    progress.refresh_from_db()
    check(progress.current_count == 3, 'count is 3 after third spin')
    check(progress.is_completed is True, 'challenge completed')
    check(progress.completed_at is not None, 'completed_at recorded')
    check(progress.reward_claimed is True, 'reward marked claimed')

    # Verify reward distributed by checking the challenge reward transaction exists
    reward_tx = Transaction.objects.filter(
        user=user1,
        balance_type='coin',
        amount=Decimal('150'),
        metadata__challenge_id=str(challenge.id),
    ).first()
    check(reward_tx is not None, 'reward transaction created for 150 coins')

    # Balance increased by AT LEAST 150 (other challenges may add more)
    final_coins = WalletService.get_balance(user1, 'coin')
    check(
        final_coins >= initial_coins + Decimal('150'),
        f'coin balance increased by at least 150 (was {initial_coins}, now {final_coins})',
    )


def test_one_time_challenge_no_replay(user1, wheel):
    section('TEST 3: one_time challenge — cannot complete twice')

    challenge = Challenge.objects.create(
        name='SMOKE_CHALL_OneTimeWelcome',
        type=Challenge.Type.WELCOME,
        recurrence=Challenge.Recurrence.ONE_TIME,
        criteria={'action': 'spin', 'target_count': 1},
        reward={'type': 'coins', 'amount': 50},
    )

    # Welcome only triggers on first spin — user1 has spins already, so should not be eligible
    seg = WheelSegment.objects.filter(wheel=wheel, position=0).first()
    Spin.objects.create(
        user=user1, wheel=wheel, stake_amount=Decimal('200'),
        payout_amount=Decimal('0'), outcome='loss',
        segment_landed=seg, reference='chall_one_time_attempt',
        server_seed_hash='xxx', nonce=99,
    )

    progress = ChallengeProgress.objects.filter(user=user1, challenge=challenge).first()
    check(
        progress is None,
        'welcome challenge skipped (user already has prior spins)',
    )


def test_welcome_for_new_user(user2, wheel):
    section('TEST 4: welcome challenge — fires on new user first spin')

    challenge = Challenge.objects.create(
        name='SMOKE_CHALL_WelcomeNew',
        type=Challenge.Type.WELCOME,
        recurrence=Challenge.Recurrence.ONE_TIME,
        criteria={'action': 'spin', 'target_count': 1},
        reward={'type': 'coins', 'amount': 100},
    )

    initial = WalletService.get_balance(user2, 'coin')
    seg = WheelSegment.objects.filter(wheel=wheel, position=0).first()
    Spin.objects.create(
        user=user2, wheel=wheel, stake_amount=Decimal('200'),
        payout_amount=Decimal('0'), outcome='loss',
        segment_landed=seg, reference='chall_welcome_new',
        server_seed_hash='yyy', nonce=1,
    )

    progress = ChallengeProgress.objects.filter(user=user2, challenge=challenge).first()
    check(progress is not None, 'welcome progress created for new user')
    check(progress.is_completed, 'welcome completed on first spin')

    # Verify the welcome reward transaction exists (other challenges may also fire)
    reward_tx = Transaction.objects.filter(
        user=user2,
        balance_type='coin',
        amount=Decimal('100'),
        metadata__challenge_id=str(challenge.id),
    ).first()
    check(reward_tx is not None, 'welcome reward transaction created for 100 coins')

    final = WalletService.get_balance(user2, 'coin')
    check(final >= initial + Decimal('100'), 'coin balance increased by at least 100')


def test_min_stake_eligibility(user2, wheel):
    section('TEST 5: min_stake eligibility — low stakes ignored')

    challenge = Challenge.objects.create(
        name='SMOKE_CHALL_HighStaker',
        type=Challenge.Type.SPIN_COUNT,
        recurrence=Challenge.Recurrence.DAILY,
        criteria={'action': 'spin', 'target_count': 1, 'min_stake': '500.00'},
        reward={'type': 'coins', 'amount': 200},
    )

    seg = WheelSegment.objects.filter(wheel=wheel, position=0).first()

    # Spin at 200 — below min_stake of 500
    Spin.objects.create(
        user=user2, wheel=wheel, stake_amount=Decimal('200'),
        payout_amount=Decimal('0'), outcome='loss',
        segment_landed=seg, reference='chall_low_stake',
        server_seed_hash='zz1', nonce=2,
    )

    progress = ChallengeProgress.objects.filter(user=user2, challenge=challenge).first()
    check(progress is None, 'low stake did not create progress')

    # Spin at 600 — above min_stake
    Spin.objects.create(
        user=user2, wheel=wheel, stake_amount=Decimal('600'),
        payout_amount=Decimal('0'), outcome='loss',
        segment_landed=seg, reference='chall_high_stake',
        server_seed_hash='zz2', nonce=3,
    )

    progress = ChallengeProgress.objects.filter(user=user2, challenge=challenge).first()
    check(progress is not None, 'qualifying stake created progress')
    check(progress.is_completed, 'challenge completed with qualifying stake')


def test_deposit_challenge(user1):
    section('TEST 6: deposit challenge — fires on deposit transaction')

    challenge = Challenge.objects.create(
        name='SMOKE_CHALL_HighRoller',
        type=Challenge.Type.DEPOSIT,
        recurrence=Challenge.Recurrence.ONE_TIME,
        criteria={'action': 'deposit', 'target_count': 1, 'min_deposit': '5000.00'},
        reward={'type': 'cash', 'amount': 500},
    )

    initial_cash = WalletService.get_balance(user1, 'cash')

    # Deposit below threshold — should not trigger
    WalletService.credit(
        user=user1, amount=Decimal('1000'), balance_type='coin',
        tx_type=Transaction.Type.DEPOSIT, reference_id='chall_dep_small',
    )
    progress = ChallengeProgress.objects.filter(user=user1, challenge=challenge).first()
    check(progress is None, 'small deposit did not trigger')

    # Deposit above threshold
    WalletService.credit(
        user=user1, amount=Decimal('5000'), balance_type='coin',
        tx_type=Transaction.Type.DEPOSIT, reference_id='chall_dep_big',
    )
    progress = ChallengeProgress.objects.filter(user=user1, challenge=challenge).first()
    check(progress is not None, 'large deposit created progress')
    check(progress.is_completed, 'deposit challenge completed')

    final_cash = WalletService.get_balance(user1, 'cash')
    check(final_cash == initial_cash + Decimal('500'), 'cash reward credited (500)')


def test_admin_list_endpoint(admin):
    section('TEST 7: Admin — list challenges endpoint')

    client = APIClient()
    client.force_authenticate(user=admin)

    res = client.get('/api/v1/admin/challenges/')
    check(res.status_code == 200, 'admin list returns 200')
    check(res.data['success'], 'success flag is True')

    data = res.data['data']
    check('results' in data, 'results in response')
    check('count' in data, 'count in response')
    check(data['count'] >= 6, 'at least 6 challenges (from earlier tests)')


def test_admin_create_endpoint(admin):
    section('TEST 8: Admin — create challenge endpoint')

    client = APIClient()
    client.force_authenticate(user=admin)

    payload = {
        'name': 'SMOKE_CHALL_AdminCreated',
        'description': 'Created via admin smoke test',
        'type': 'spin_count',
        'recurrence': 'daily',
        'criteria': {'action': 'spin', 'target_count': 5, 'per': 'day'},
        'reward': {'type': 'coins', 'amount': 200},
        'is_active': True,
        'is_visible': True,
    }

    res = client.post('/api/v1/admin/challenges/', payload, format='json')
    check(res.status_code == 201, 'admin create returns 201')
    check(res.data['success'], 'success flag is True')
    check('id' in res.data['data'], 'returns new challenge id')

    # Validation: missing required field
    bad = client.post('/api/v1/admin/challenges/', {'name': 'incomplete'}, format='json')
    check(bad.status_code == 400, 'rejects incomplete payload')
    check(bad.data['code'] == 'MISSING_FIELDS', 'MISSING_FIELDS error code')

    # Validation: invalid type
    invalid = client.post('/api/v1/admin/challenges/', {
        **payload, 'type': 'invalid_type', 'name': 'SMOKE_CHALL_Invalid',
    }, format='json')
    check(invalid.status_code == 400, 'rejects invalid type')
    check(invalid.data['code'] == 'INVALID_TYPE', 'INVALID_TYPE error code')


def test_admin_update_and_delete(admin):
    section('TEST 9: Admin — update + soft delete endpoints')

    client = APIClient()
    client.force_authenticate(user=admin)

    # Create one to update
    create_res = client.post('/api/v1/admin/challenges/', {
        'name': 'SMOKE_CHALL_ToUpdate',
        'type': 'spin_count',
        'recurrence': 'daily',
        'criteria': {'action': 'spin', 'target_count': 5},
        'reward': {'type': 'coins', 'amount': 100},
    }, format='json')
    challenge_id = create_res.data['data']['id']

    # PATCH
    update_res = client.patch(
        f'/api/v1/admin/challenges/{challenge_id}/',
        {'name': 'SMOKE_CHALL_Updated', 'is_visible': False},
        format='json',
    )
    check(update_res.status_code == 200, 'PATCH returns 200')
    check(update_res.data['data']['name'] == 'SMOKE_CHALL_Updated', 'name updated')
    check(update_res.data['data']['is_visible'] is False, 'is_visible toggled')

    # DELETE (soft)
    del_res = client.delete(f'/api/v1/admin/challenges/{challenge_id}/')
    check(del_res.status_code == 200, 'DELETE returns 200')

    # Verify soft delete
    challenge = Challenge.objects.get(id=challenge_id)
    check(challenge.is_active is False, 'is_active set to False (soft delete)')

    # 404 on non-existent
    import uuid
    fake = uuid.uuid4()
    res_404 = client.get(f'/api/v1/admin/challenges/{fake}/')
    check(res_404.status_code == 404, '404 on non-existent challenge')


def test_admin_participants_and_completions(admin, user1):
    section('TEST 10: Admin — participants + completions endpoints')

    client = APIClient()
    client.force_authenticate(user=admin)

    # Use the challenge from test 2 (SMOKE_CHALL_SpinFive)
    challenge = Challenge.objects.get(name='SMOKE_CHALL_SpinFive')

    res = client.get(f'/api/v1/admin/challenges/{challenge.id}/participants/')
    check(res.status_code == 200, 'participants endpoint returns 200')
    check(len(res.data['data']['results']) >= 1, 'has at least 1 participant')

    comp_res = client.get(f'/api/v1/admin/challenges/{challenge.id}/completions/')
    check(comp_res.status_code == 200, 'completions endpoint returns 200')
    check(len(comp_res.data['data']['results']) >= 1, 'has at least 1 completion')


def test_player_endpoints(user1):
    section('TEST 11: Player — list + detail endpoints')

    client = APIClient()
    client.force_authenticate(user=user1)

    res = client.get('/api/v1/challenges/')
    check(res.status_code == 200, 'player list returns 200')
    check(res.data['success'], 'success True')
    check('challenges' in res.data['data'], 'challenges key in response')

    # Find a visible challenge
    challenge = Challenge.objects.filter(
        name__startswith='SMOKE_CHALL_', is_active=True, is_visible=True,
    ).first()

    if challenge:
        res = client.get(f'/api/v1/challenges/{challenge.id}/')
        check(res.status_code == 200, 'player detail returns 200')
        check(res.data['data']['id'] == str(challenge.id), 'correct challenge returned')
        check('my_progress' in res.data['data'], 'my_progress in detail response')


def test_non_staff_blocked_from_admin(user1):
    section('TEST 12: Non-staff blocked from admin endpoints')

    client = APIClient()
    client.force_authenticate(user=user1)

    res = client.get('/api/v1/admin/challenges/')
    check(res.status_code == 403, 'non-staff gets 403 on admin list')

    res = client.post('/api/v1/admin/challenges/', {}, format='json')
    check(res.status_code == 403, 'non-staff gets 403 on admin create')


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print('SPIN REWARDS — CHALLENGES SMOKE TEST')
    print('Testing engine, signals, and all endpoints.\n')

    print('→ Creating test data...')
    admin, user1, user2, wheel = create_test_data()
    print(f'  Admin: {admin.telegram_id}')
    print(f'  User1: {user1.telegram_id}')
    print(f'  User2: {user2.telegram_id}')
    print(f'  Wheel: {wheel.name}')

    try:
        test_window_calculation()
        test_spin_count_challenge(user1, wheel)
        test_one_time_challenge_no_replay(user1, wheel)
        test_welcome_for_new_user(user2, wheel)
        test_min_stake_eligibility(user2, wheel)
        test_deposit_challenge(user1)
        test_admin_list_endpoint(admin)
        test_admin_create_endpoint(admin)
        test_admin_update_and_delete(admin)
        test_admin_participants_and_completions(admin, user1)
        test_player_endpoints(user1)
        test_non_staff_blocked_from_admin(user1)

    finally:
        print('\n→ Cleaning up test data...')
        cleanup()
        print('  Done.')

    print(f'\n{"=" * 60}')
    print(f'RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print(f'{"=" * 60}')

    if TESTS_FAILED:
        print('\n❌ CHALLENGES SMOKE TEST FAILED')
        sys.exit(1)
    else:
        print('\n✅ CHALLENGES SMOKE TEST PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()