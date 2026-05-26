"""
Admin Dashboard API smoke test.

Tests all 17 endpoints across all 8 screens. Uses Django test client
directly — no network calls needed.

Creates isolated test data, runs all endpoints, tears down cleanly.

Usage:
    docker compose exec api python smoke_test_admin.py
"""
import os
import sys
from datetime import date
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from django.test import RequestFactory, override_settings
from django.utils import timezone

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


# ─── Test data setup ──────────────────────────────────────────────────────────

from apps.kyc.models import BankAccount, KYCDocument, KYCProfile
from apps.spin.models import Spin, Wheel, WheelSegment
from apps.users.models import User
from apps.wallet.models import Transaction
from apps.wallet.services import WalletService
from apps.withdrawals.models import Withdrawal


def cleanup():
    """Remove all smoke test data."""
    for tid in [900000001, 900000002, 900000099]:
        try:
            u = User.objects.get(telegram_id=tid)
            Withdrawal.objects.filter(user=u).delete()
            BankAccount.objects.filter(user=u).delete()
            KYCProfile.objects.filter(user=u).delete()
            KYCDocument.objects.filter(user=u).delete()
            Spin.objects.filter(user=u).delete()
            u.delete()
        except User.DoesNotExist:
            pass

    # Clean up test wheels
    Wheel.objects.filter(name__startswith='SMOKE_ADMIN_').delete()


def create_test_data():
    """
    Create the test fixtures the smoke test needs:
      - admin_user    (is_staff=True)
      - regular_user  (with KYC approved, bank account, spins, withdrawal)
      - wheel         (with segments)
    """
    cleanup()

    # ── Admin user ──────────────────────────────────────────────────────
    admin = User.objects.create_user(
        telegram_id=900000099,
        first_name='Smoke',
        username='smoke_admin',
    )
    admin.is_staff = True
    admin.save()

    # ── Regular user with full data ─────────────────────────────────────
    user = User.objects.create_user(
        telegram_id=900000001,
        first_name='SmokeUser',
        last_name='Test',
        username='smoke_user_1',
    )

    # KYC profile — approved
    kyc = KYCProfile.objects.create(
        user=user,
        full_name='SMOKEUSER TEST',
        nin='12345678901',
        bvn='12345678901',
        date_of_birth=date(1990, 1, 1),
        phone_number='08012345678',
        personal_info_status=KYCProfile.SectionStatus.VERIFIED,
        bank_account_status=KYCProfile.SectionStatus.VERIFIED,
        document_status=KYCProfile.SectionStatus.VERIFIED,
        submitted_at=timezone.now(),
    )

    # Bank account
    bank = BankAccount.objects.create(
        user=user,
        bank_code='058',
        bank_name='GTBank',
        account_number='1234567890',
        account_name='SMOKEUSER TEST',
        is_active=True,
        verified_at=timezone.now(),
    )

    # Wallet — credit some cash and coins
    WalletService.credit(
        user=user, amount=Decimal('50000'), balance_type='cash',
        tx_type=Transaction.Type.WIN,
        reference_id='smoke_admin_cash_init',
        metadata={'source': 'smoke_test'},
    )
    WalletService.credit(
        user=user, amount=Decimal('10000'), balance_type='coin',
        tx_type=Transaction.Type.DEPOSIT,
        reference_id='smoke_admin_coin_init',
        metadata={'source': 'smoke_test'},
    )

    # Test wheel
    # is_active=False to skip overlap validation with production seeded wheels.
    # The smoke test still creates real segments and spins on this wheel.
    wheel = Wheel.objects.create(
        name='SMOKE_ADMIN_Standard',
        wheel_type=Wheel.WheelType.STANDARD,
        currency_type=Wheel.CurrencyType.COIN,
        min_stake=Decimal('200'),
        max_stake=Decimal('500'),
        rtp_target=Decimal('80'),
        is_active=False,
        is_welcome_only=False,
    )
    WheelSegment.objects.create(
        wheel=wheel, position=0, label='Loss',
        multiplier=Decimal('0'), probability_weight=60, color='#3a3a3a',
    )
    WheelSegment.objects.create(
        wheel=wheel, position=1, label='2x',
        multiplier=Decimal('2'), probability_weight=30, color='#1A237E',
    )
    WheelSegment.objects.create(
        wheel=wheel, position=2, label='5x',
        multiplier=Decimal('5'), probability_weight=10, color='#C9961A',
    )
    seg_win = WheelSegment.objects.get(wheel=wheel, position=1)

    # Create a few spins
    Spin.objects.create(
        user=user,
        wheel=wheel,
        stake_amount=Decimal('500'),
        payout_amount=Decimal('1000'),
        outcome='win',
        segment_landed=seg_win,
        reference='smoke_spin_001',
        server_seed_hash='abc123',
        nonce=1,
    )
    seg_loss = WheelSegment.objects.get(wheel=wheel, position=0)
    Spin.objects.create(
        user=user,
        wheel=wheel,
        stake_amount=Decimal('300'),
        payout_amount=Decimal('0'),
        outcome='loss',
        segment_landed=seg_loss,
        reference='smoke_spin_002',
        server_seed_hash='def456',
        nonce=2,
    )

    # Create a withdrawal in pending_review state
    import secrets as _secrets
    ref = f'wd_{_secrets.token_urlsafe(16)}'
    debit_tx = WalletService.debit(
        user=user, amount=Decimal('15000'), balance_type='cash',
        tx_type=Transaction.Type.WITHDRAWAL,
        reference_id=ref, metadata={'source': 'smoke_test'},
    )
    withdrawal = Withdrawal.objects.create(
        user=user,
        bank_account=bank,
        amount=Decimal('15000'),
        fee=Decimal('0'),
        net_amount=Decimal('15000'),
        status=Withdrawal.Status.PENDING_REVIEW,
        reference=ref,
        requires_review=True,
        forced_manual_review=False,
        debit_transaction=debit_tx,
    )

    # Second user — no KYC (for flagged/pending test)
    user2 = User.objects.create_user(
        telegram_id=900000002,
        first_name='SmokeUser2',
        username='smoke_user_2',
    )

    return admin, user, user2, wheel, withdrawal, kyc


# ─── View invocation helper ────────────────────────────────────────────────────

from rest_framework.test import APIClient


def call_view(view_class, method, url, user, data=None, **kwargs):
    """
    Invoke an admin view using DRF APIClient with force_authenticate.
    This correctly runs the full DRF permission stack including IsAdminUser.
    """
    client = APIClient()
    client.force_authenticate(user=user)

    if method == 'GET':
        response = client.get(url)
    elif method == 'POST':
        response = client.post(url, data=data or {}, format='json')
    elif method == 'PUT':
        response = client.put(url, data=data or {}, format='json')
    else:
        raise ValueError(f'Unknown method: {method}')

    return response


# ─── Import all views ─────────────────────────────────────────────────────────

from apps.admin_panel.views import (
    AdminAuditLogsView,
    AdminFraudMonitorView,
    AdminKYCApproveView,
    AdminKYCQueueView,
    AdminKYCRejectView,
    AdminUserDetailView,
    AdminUserSpinsView,
    AdminUserTransactionsView,
    AdminUsersListView,
    AdminWithdrawalApproveView,
    AdminWithdrawalRejectView,
    AdminWithdrawalsListView,
    DashboardView,
    FinancialsView,
    RTPControlDetailView,
    RTPControlListCreateView,
)


# ─── Tests ────────────────────────────────────────────────────────────────────

def test_permission_enforcement(admin, regular_user):
    section('TEST 1: Permission enforcement — non-staff blocked')

    # Non-staff user should get 403
    res = call_view(DashboardView, 'GET', '/api/v1/admin/dashboard/', regular_user)
    check(res.status_code == 403, 'non-staff user gets 403 on dashboard')

    res = call_view(AdminUsersListView, 'GET', '/api/v1/admin/users/', regular_user)
    check(res.status_code == 403, 'non-staff user gets 403 on users list')

    res = call_view(AdminWithdrawalsListView, 'GET', '/api/v1/admin/withdrawals/', regular_user)
    check(res.status_code == 403, 'non-staff user gets 403 on withdrawals')

    # Admin user should pass
    res = call_view(DashboardView, 'GET', '/api/v1/admin/dashboard/', admin)
    if res.status_code != 200:
        print(f'  [DEBUG] Admin dashboard returned {res.status_code}: {getattr(res, "data", res.content[:300])}')
    check(res.status_code == 200, 'admin user gets 200 on dashboard')


def test_dashboard(admin):
    section('TEST 2: Screen 1 — Dashboard')

    res = call_view(DashboardView, 'GET', '/api/v1/admin/dashboard/', admin)

    if res.status_code != 200:
        print(f'  [DEBUG] Dashboard returned {res.status_code}: {getattr(res, "data", res.content[:300])}')

    data = res.data

    check(res.status_code == 200, 'returns 200')
    check(data['success'] is True, 'success flag is True')

    d = data['data']
    check('kpis' in d, 'kpis section present')
    check('profit_trend' in d, 'profit_trend section present')
    check('recent_spins' in d, 'recent_spins section present')
    check('top_winners' in d, 'top_winners section present')

    kpis = d['kpis']
    check('total_revenue' in kpis, 'total_revenue in kpis')
    check('net_profit' in kpis, 'net_profit in kpis')
    check('current_rtp' in kpis, 'current_rtp in kpis')
    check('active_users' in kpis, 'active_users in kpis')
    check('new_users_today' in kpis, 'new_users_today in kpis')

    # recent_spins should contain our smoke test spin
    check(isinstance(d['recent_spins'], list), 'recent_spins is a list')
    if d['recent_spins']:
        spin = d['recent_spins'][0]
        check('user' in spin, 'spin has user field')
        check('stake' in spin, 'spin has stake field')
        check('outcome' in spin, 'spin has outcome field')
        check('win_value' in spin, 'spin has win_value field')


def test_financials(admin):
    section('TEST 3: Screen 2 — Financials')

    res = call_view(FinancialsView, 'GET', '/api/v1/admin/financials/', admin)
    data = res.data

    check(res.status_code == 200, 'returns 200')
    check(data['success'] is True, 'success flag is True')

    d = data['data']
    check('kpis' in d, 'kpis section present')
    check('spins_breakdown' in d, 'spins_breakdown present')
    check('cash_flow' in d, 'cash_flow present')

    kpis = d['kpis']
    check('total_deposits' in kpis, 'total_deposits in kpis')
    check('total_withdrawals' in kpis, 'total_withdrawals in kpis')
    check('pending_withdrawals' in kpis, 'pending_withdrawals in kpis')

    breakdown = d['spins_breakdown']
    check('total_staked' in breakdown, 'total_staked in breakdown')
    check('total_won' in breakdown, 'total_won in breakdown')
    check('house_fees' in breakdown, 'house_fees in breakdown')
    check('spin_count_total' in breakdown, 'spin_count_total in breakdown')

    # Verify math is correct — house_fees = staked - won
    staked = Decimal(breakdown['total_staked'])
    won = Decimal(breakdown['total_won'])
    fees = Decimal(breakdown['house_fees'])
    check(
        abs((staked - won) - fees) < Decimal('0.01'),
        f'house_fees = staked - won ({staked} - {won} = {fees})',
    )

    # cash_flow has both deposits and withdrawals arrays
    cf = d['cash_flow']
    check('deposits' in cf, 'cash_flow.deposits present')
    check('withdrawals' in cf, 'cash_flow.withdrawals present')


def test_rtp_list(admin, wheel):
    section('TEST 4: Screen 3 — RTP Control list')

    res = call_view(RTPControlListCreateView, 'GET', '/api/v1/admin/rtp/', admin)
    data = res.data

    check(res.status_code == 200, 'returns 200')
    check('wheels' in data['data'], 'wheels key present')
    check(data['data']['count'] > 0, 'at least one wheel returned')

    # Find our smoke wheel
    wheels = data['data']['wheels']
    smoke_wheel = next((w for w in wheels if 'SMOKE_ADMIN' in w['name']), None)
    check(smoke_wheel is not None, 'smoke test wheel found in list')

    if smoke_wheel:
        check('segments' in smoke_wheel, 'wheel has segments')
        check(len(smoke_wheel['segments']) == 3, 'smoke wheel has 3 segments')
        check('probability_weight' in smoke_wheel['segments'][0], 'segment has probability_weight')
        check('probability_pct' in smoke_wheel['segments'][0], 'segment has probability_pct')
        check('house_edge' in smoke_wheel, 'wheel has house_edge')
        check('computed_rtp' in smoke_wheel, 'wheel has computed_rtp')

        # Verify probability_pct sums to ~100
        total_pct = sum(s['probability_pct'] for s in smoke_wheel['segments'])
        check(abs(total_pct - 100) < 0.1, f'probability_pct sums to ~100 (got {total_pct})')


def test_rtp_create(admin):
    section('TEST 5: Screen 3 — RTP Control create')

    payload = {
        'name': 'SMOKE_ADMIN_Created',
        'wheel_type': 'standard',
        'currency_type': 'coin',
        'min_stake': '1000',
        'max_stake': '5000',
        'rtp_target': '75',
        'is_active': False,
        'segments': [
            {'label': 'Loss', 'multiplier': '0',   'probability_weight': 50, 'color': '#3a3a3a'},
            {'label': '2x',   'multiplier': '2',   'probability_weight': 30, 'color': '#1A237E'},
            {'label': '5x',   'multiplier': '5',   'probability_weight': 15, 'color': '#C9961A'},
            {'label': '10x',  'multiplier': '10',  'probability_weight': 5,  'color': '#FFD700'},
        ],
    }

    res = call_view(RTPControlListCreateView, 'POST', '/api/v1/admin/rtp/', admin, data=payload)
    data = res.data

    check(res.status_code == 201, 'creates wheel — returns 201')
    check(data['success'] is True, 'success is True')
    check('id' in data['data'], 'returns new wheel id')

    # Clean up the created wheel
    if 'id' in data.get('data', {}):
        try:
            Wheel.objects.filter(id=data['data']['id']).delete()
        except Exception:
            pass

    # Validation: missing segments
    bad_payload = {**payload, 'segments': []}
    res_bad = call_view(RTPControlListCreateView, 'POST', '/api/v1/admin/rtp/', admin, data=bad_payload)
    check(res_bad.status_code == 400, 'rejects payload with no segments')
    check(res_bad.data['code'] == 'MISSING_SEGMENTS', 'error code MISSING_SEGMENTS')


def test_rtp_edit(admin, wheel):
    section('TEST 6: Screen 3 — RTP Control edit')

    # Only update rtp_target and segments — don't toggle is_active since
    # activating the wheel would collide with the production Standard Wheel range.
    payload = {
        'rtp_target': '85',
        'segments': [
            {'position': 0, 'probability_weight': 55},
            {'position': 1, 'probability_weight': 35},
            {'position': 2, 'probability_weight': 10},
        ],
    }

    res = call_view(
        RTPControlDetailView, 'PUT', f'/api/v1/admin/rtp/{wheel.id}/',
        admin, data=payload, wheel_id=wheel.id,
    )

    check(res.status_code == 200, 'edit wheel returns 200')
    check(res.data['success'] is True, 'success is True')

    # Verify changes were applied
    wheel.refresh_from_db()
    check(str(wheel.rtp_target) == '85.00', 'rtp_target updated to 85')
    check(wheel.is_active is False, 'is_active unchanged (still False)')

    # Revert rtp_target for other tests
    wheel.rtp_target = Decimal('80')
    wheel.save()

    # 404 on non-existent wheel
    import uuid
    fake_id = uuid.uuid4()
    res_404 = call_view(
        RTPControlDetailView, 'PUT', f'/api/v1/admin/rtp/{fake_id}/',
        admin, data={}, wheel_id=fake_id,
    )
    check(res_404.status_code == 404, '404 on non-existent wheel')


def test_users_list(admin, user, user2):
    section('TEST 7: Screen 4 — Users list')

    res = call_view(AdminUsersListView, 'GET', '/api/v1/admin/users/', admin)
    data = res.data

    check(res.status_code == 200, 'returns 200')
    check('overview' in data['data'], 'overview section present')
    check('results' in data['data'], 'results (paginated users) present')
    check('count' in data['data'], 'count present')

    overview = data['data']['overview']
    check('total_users' in overview, 'total_users in overview')
    check('flagged_accounts' in overview, 'flagged_accounts in overview')
    check('pending_kyc' in overview, 'pending_kyc in overview')
    check(overview['total_users'] >= 2, 'at least 2 users (our smoke users)')

    # Check user shape
    results = data['data']['results']
    if results:
        u = results[0]
        for field in ['id', 'name', 'registered_on', 'balance', 'kyc_status', 'risk']:
            check(field in u, f'user result has {field}')

    # Search filter
    _c = APIClient(); _c.force_authenticate(user=admin)
    res_search = _c.get('/api/v1/admin/users/', {'search': 'SmokeUser'})
    check(res_search.status_code == 200, 'search filter works')
    check(
        res_search.data['data']['count'] >= 1,
        'search finds smoke users',
    )

    # KYC pending filter
    res_filter = _c.get('/api/v1/admin/users/', {'filter': 'kyc_pending'})
    check(res_filter.status_code == 200, 'filter=kyc_pending works')


def test_user_detail(admin, user):
    section('TEST 8: Screen 4 — User detail')

    res = call_view(
        AdminUserDetailView, 'GET', f'/api/v1/admin/users/{user.id}/',
        admin, user_id=user.id,
    )
    data = res.data

    check(res.status_code == 200, 'returns 200')
    check(data['data']['telegram_id'] == user.telegram_id, 'correct user returned')
    check('kyc' in data['data'], 'kyc section present')
    check('bank_account' in data['data'], 'bank_account section present')
    check('cash_balance' in data['data'], 'cash_balance present')
    check('coin_balance' in data['data'], 'coin_balance present')
    check(
        data['data']['kyc']['overall_status'] == 'approved',
        'KYC status is approved',
    )
    check(
        data['data']['bank_account']['bank_name'] == 'GTBank',
        'bank account name correct',
    )

    # 404 on non-existent user (use fake UUID since User pk is UUID)
    import uuid as _uuid
    fake_user_id = _uuid.uuid4()
    res_404 = call_view(
        AdminUserDetailView, 'GET', f'/api/v1/admin/users/{fake_user_id}/',
        admin, user_id=fake_user_id,
    )
    check(res_404.status_code == 404, '404 on non-existent user')


def test_user_spins(admin, user):
    section('TEST 9: Screen 4 — User spins')

    res = call_view(
        AdminUserSpinsView, 'GET', f'/api/v1/admin/users/{user.id}/spins/',
        admin, user_id=user.id,
    )
    data = res.data

    check(res.status_code == 200, 'returns 200')
    check('results' in data['data'], 'results present')
    check(data['data']['count'] >= 2, 'found at least 2 smoke spins')

    if data['data']['results']:
        spin = data['data']['results'][0]
        for field in ['wheel', 'stake', 'outcome', 'win_value', 'date']:
            check(field in spin, f'spin result has {field}')

    # Recent spin shows correct outcome
    spins = data['data']['results']
    outcomes = [s['outcome'] for s in spins]
    check('win' in outcomes, 'win spin is in history')
    check('loss' in outcomes, 'loss spin is in history')


def test_user_transactions(admin, user):
    section('TEST 10: Screen 4 — User transactions')

    res = call_view(
        AdminUserTransactionsView, 'GET',
        f'/api/v1/admin/users/{user.id}/transactions/',
        admin, user_id=user.id,
    )
    data = res.data

    check(res.status_code == 200, 'returns 200')
    check('results' in data['data'], 'results present')
    check(data['data']['count'] >= 2, 'found transactions (credits + debit)')

    if data['data']['results']:
        tx = data['data']['results'][0]
        for field in ['type', 'label', 'amount', 'is_credit', 'date']:
            check(field in tx, f'transaction has {field}')

    # Check is_credit is set correctly
    txs = data['data']['results']
    credits = [t for t in txs if t['is_credit']]
    debits = [t for t in txs if not t['is_credit']]
    check(len(credits) > 0, 'at least one credit transaction')
    check(len(debits) > 0, 'at least one debit transaction')


def test_withdrawals_list(admin, withdrawal):
    section('TEST 11: Screen 5 — Withdrawal Management list')

    res = call_view(
        AdminWithdrawalsListView, 'GET', '/api/v1/admin/withdrawals/', admin,
    )
    data = res.data

    check(res.status_code == 200, 'returns 200')
    check('overview' in data['data'], 'overview present')
    check('results' in data['data'], 'results present')

    overview = data['data']['overview']
    check('total_pending' in overview, 'total_pending in overview')
    check('total_paid' in overview, 'total_paid in overview')
    check('queued' in overview, 'queued count in overview')
    check(overview['queued'] >= 1, 'queued count includes our smoke withdrawal')

    # Our smoke withdrawal should be in results
    results = data['data']['results']
    smoke_wd = next(
        (w for w in results if str(w['id']) == str(withdrawal.id)), None
    )
    check(smoke_wd is not None, 'smoke withdrawal found in list')
    if smoke_wd:
        check(smoke_wd['status'] == 'pending_review', 'status is pending_review')
        check(smoke_wd['risk'] == 'Medium', 'risk correctly Medium for ₦15k amount')
        check(smoke_wd['type'] == 'Medium', 'type correctly Medium')

    # Status filter
    _c2 = APIClient(); _c2.force_authenticate(user=admin)
    res_filter = _c2.get('/api/v1/admin/withdrawals/', {'status': 'pending_review'})
    check(res_filter.status_code == 200, 'status filter works')
    check(
        res_filter.data['data']['count'] >= 1,
        'pending_review filter returns results',
    )


def test_withdrawal_approve(admin, withdrawal):
    section('TEST 12: Screen 5 — Withdrawal approve')

    # Can't approve a completed withdrawal — first confirm our withdrawal is in right state
    withdrawal.refresh_from_db()
    check(
        withdrawal.status == Withdrawal.Status.PENDING_REVIEW,
        'withdrawal is in pending_review before test',
    )

    res = call_view(
        AdminWithdrawalApproveView,
        'POST',
        f'/api/v1/admin/withdrawals/{withdrawal.id}/approve/',
        admin,
        data={'notes': 'Smoke test approval'},
        withdrawal_id=withdrawal.id,
    )

    check(res.status_code == 200, 'approve returns 200')
    check(res.data['success'] is True, 'success is True')

    # Status should have changed
    withdrawal.refresh_from_db()
    check(
        withdrawal.status in ['pending', 'processing', 'completed'],
        f'withdrawal moved past pending_review (now: {withdrawal.status})',
    )
    check(withdrawal.reviewed_by == admin, 'reviewed_by set to admin')
    check(withdrawal.reviewed_at is not None, 'reviewed_at recorded')

    # Approving again should fail (already past pending_review)
    res_again = call_view(
        AdminWithdrawalApproveView,
        'POST',
        f'/api/v1/admin/withdrawals/{withdrawal.id}/approve/',
        admin,
        data={},
        withdrawal_id=withdrawal.id,
    )
    check(res_again.status_code == 400, 'double-approve returns 400')
    check(res_again.data['code'] == 'CANNOT_APPROVE', 'correct error code')


def test_withdrawal_reject(admin, user, bank):
    section('TEST 13: Screen 5 — Withdrawal reject')

    # Create a fresh pending_review withdrawal for rejection test
    import secrets as _sec
    ref2 = f'wd_{_sec.token_urlsafe(16)}'
    debit = WalletService.debit(
        user=user, amount=Decimal('5000'), balance_type='cash',
        tx_type=Transaction.Type.WITHDRAWAL,
        reference_id=ref2, metadata={'source': 'smoke_test'},
    )
    wd2 = Withdrawal.objects.create(
        user=user, bank_account=bank,
        amount=Decimal('5000'), fee=Decimal('0'), net_amount=Decimal('5000'),
        status=Withdrawal.Status.PENDING_REVIEW,
        reference=ref2, requires_review=True,
        debit_transaction=debit,
    )

    cash_before = WalletService.get_balance(user, 'cash')

    # Reject without reason — should fail
    res_no_reason = call_view(
        AdminWithdrawalRejectView,
        'POST',
        f'/api/v1/admin/withdrawals/{wd2.id}/reject/',
        admin, data={},
        withdrawal_id=wd2.id,
    )
    check(res_no_reason.status_code == 400, 'reject without reason returns 400')
    check(res_no_reason.data['code'] == 'REASON_REQUIRED', 'REASON_REQUIRED error code')

    # Reject with reason — should succeed
    res = call_view(
        AdminWithdrawalRejectView,
        'POST',
        f'/api/v1/admin/withdrawals/{wd2.id}/reject/',
        admin,
        data={'reason': 'Suspicious activity — smoke test'},
        withdrawal_id=wd2.id,
    )
    check(res.status_code == 200, 'reject returns 200')
    check(res.data['success'] is True, 'success is True')

    wd2.refresh_from_db()
    check(wd2.status == Withdrawal.Status.REJECTED, 'status is rejected')
    check(wd2.refund_transaction is not None, 'refund_transaction recorded')

    # Cash refunded
    cash_after = WalletService.get_balance(user, 'cash')
    check(
        cash_after == cash_before + Decimal('5000'),
        f'cash refunded after rejection (before={cash_before}, after={cash_after})',
    )


def test_kyc_queue(admin):
    section('TEST 14: Screen 6 — KYC Review Queue')

    res = call_view(AdminKYCQueueView, 'GET', '/api/v1/admin/kyc/queue/', admin)
    data = res.data

    check(res.status_code == 200, 'returns 200')
    check('overview' in data['data'], 'overview present')
    check('results' in data['data'], 'results present')

    overview = data['data']['overview']
    check('total_pending' in overview, 'total_pending in overview')
    check('total_approved' in overview, 'total_approved in overview')
    check('total_rejected' in overview, 'total_rejected in overview')
    check(overview['total_approved'] >= 1, 'at least 1 approved (smoke user)')

    # Check result shape
    results = data['data']['results']
    if results:
        item = results[0]
        check('sections' in item, 'KYC item has sections')
        check('personal_info' in item['sections'], 'has personal_info section')
        check('bank_account' in item['sections'], 'has bank_account section')
        check('document' in item['sections'], 'has document section')
        check('overall_status' in item, 'has overall_status')
        check('can_withdraw' in item, 'has can_withdraw flag')

    # Status filter
    _c3 = APIClient(); _c3.force_authenticate(user=admin)
    res_filter = _c3.get('/api/v1/admin/kyc/queue/', {'status': 'approved'})
    check(res_filter.status_code == 200, 'status=approved filter works')
    check(
        res_filter.data['data']['count'] >= 1,
        'approved filter returns at least our smoke user',
    )


def test_kyc_approve(admin, kyc):
    section('TEST 15: Screen 6 — KYC approve section')

    # Set personal_info to requires_correction first
    kyc.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    kyc.personal_info_reason = 'Test mismatch'
    kyc.save()

    # Approve just personal_info
    res = call_view(
        AdminKYCApproveView,
        'POST',
        f'/api/v1/admin/kyc/{kyc.id}/approve/',
        admin,
        data={'section': 'personal_info'},
        kyc_id=kyc.id,
    )
    check(res.status_code == 200, 'approve section returns 200')
    check(res.data['success'] is True, 'success is True')
    check('overall_status' in res.data['data'], 'overall_status in response')

    kyc.refresh_from_db()
    check(
        kyc.personal_info_status == KYCProfile.SectionStatus.VERIFIED,
        'personal_info_status back to verified',
    )
    check(kyc.personal_info_reason == '', 'reason cleared')

    # Invalid section
    res_bad = call_view(
        AdminKYCApproveView,
        'POST',
        f'/api/v1/admin/kyc/{kyc.id}/approve/',
        admin,
        data={'section': 'invalid_section'},
        kyc_id=kyc.id,
    )
    check(res_bad.status_code == 400, 'invalid section returns 400')
    check(res_bad.data['code'] == 'INVALID_SECTION', 'INVALID_SECTION error code')

    # 404 on non-existent KYC
    import uuid
    fake_id = uuid.uuid4()
    res_404 = call_view(
        AdminKYCApproveView, 'POST',
        f'/api/v1/admin/kyc/{fake_id}/approve/',
        admin, data={'section': 'all'}, kyc_id=fake_id,
    )
    check(res_404.status_code == 404, '404 on non-existent KYC')


def test_kyc_reject(admin, kyc):
    section('TEST 16: Screen 6 — KYC reject section')

    # Reject without reason — should fail
    res_no_reason = call_view(
        AdminKYCRejectView,
        'POST',
        f'/api/v1/admin/kyc/{kyc.id}/reject/',
        admin,
        data={'section': 'document'},
        kyc_id=kyc.id,
    )
    check(res_no_reason.status_code == 400, 'reject without reason returns 400')
    check(res_no_reason.data['code'] == 'REASON_REQUIRED', 'REASON_REQUIRED error code')

    # Reject with reason
    res = call_view(
        AdminKYCRejectView,
        'POST',
        f'/api/v1/admin/kyc/{kyc.id}/reject/',
        admin,
        data={'section': 'document', 'reason': 'Document looks fake'},
        kyc_id=kyc.id,
    )
    check(res.status_code == 200, 'reject section returns 200')
    check(res.data['success'] is True, 'success is True')

    kyc.refresh_from_db()
    check(
        kyc.document_status == KYCProfile.SectionStatus.REJECTED,
        'document_status is rejected',
    )
    check(kyc.document_reason == 'Document looks fake', 'rejection reason stored')
    check(
        res.data['data']['can_withdraw'] is False,
        'can_withdraw is False after rejection',
    )

    # Restore KYC for other tests
    kyc.document_status = KYCProfile.SectionStatus.VERIFIED
    kyc.document_reason = ''
    kyc.save()


def test_fraud_monitor(admin):
    section('TEST 17: Screen 7 — Fraud & Risk Monitor')

    res = call_view(AdminFraudMonitorView, 'GET', '/api/v1/admin/fraud/', admin)
    data = res.data

    check(res.status_code == 200, 'returns 200')
    check('total_flagged' in data['data'], 'total_flagged present')
    check('flagged_users' in data['data'], 'flagged_users present')
    check(isinstance(data['data']['flagged_users'], list), 'flagged_users is a list')

    if data['data']['flagged_users']:
        flagged = data['data']['flagged_users'][0]
        check('name' in flagged, 'flagged user has name')
        check('flag' in flagged, 'flagged user has flag description')
        check('risk' in flagged, 'flagged user has risk level')
        check('flag_type' in flagged, 'flagged user has flag_type')
        check(
            flagged['risk'] in ['Low', 'Medium', 'High'],
            f'risk is valid value: {flagged["risk"]}',
        )


def test_audit_logs(admin):
    section('TEST 18: Screen 8 — Audit Logs')

    res = call_view(AdminAuditLogsView, 'GET', '/api/v1/admin/audit-logs/', admin)
    data = res.data

    check(res.status_code == 200, 'returns 200')
    check('results' in data['data'], 'results present')
    check('count' in data['data'], 'count present')

    # If there are logs (withdrawal and KYC actions from our tests)
    results = data['data']['results']
    if results:
        log = results[0]
        check('type' in log, 'log has type')
        check('action' in log, 'log has action')
        check('target_user' in log, 'log has target_user')
        check('performed_by' in log, 'log has performed_by')
        check('timestamp' in log, 'log has timestamp')
        check('timestamp_display' in log, 'log has timestamp_display')

    # Type filter
    _c4 = APIClient(); _c4.force_authenticate(user=admin)
    res_filter = _c4.get('/api/v1/admin/audit-logs/', {'type': 'withdrawal'})
    check(res_filter.status_code == 200, 'type=withdrawal filter works')

    res_kyc = _c4.get('/api/v1/admin/audit-logs/', {'type': 'kyc'})
    check(res_kyc.status_code == 200, 'type=kyc filter works')


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print('SPIN REWARDS — ADMIN DASHBOARD SMOKE TEST')
    print('Testing all 17 endpoints across 8 screens.\n')

    print('→ Creating test data...')
    admin, user, user2, wheel, withdrawal, kyc = create_test_data()
    bank = BankAccount.objects.get(user=user, is_active=True)
    print(f'  Admin:   telegram_id={admin.telegram_id}')
    print(f'  User:    telegram_id={user.telegram_id}')
    print(f'  Wheel:   {wheel.name}')
    print(f'  Withdrawal: {withdrawal.id} (pending_review)')

    try:
        # All tests in order
        test_permission_enforcement(admin, user)
        test_dashboard(admin)
        test_financials(admin)
        test_rtp_list(admin, wheel)
        test_rtp_create(admin)
        test_rtp_edit(admin, wheel)
        test_users_list(admin, user, user2)
        test_user_detail(admin, user)
        test_user_spins(admin, user)
        test_user_transactions(admin, user)
        test_withdrawals_list(admin, withdrawal)
        test_withdrawal_approve(admin, withdrawal)
        test_withdrawal_reject(admin, user, bank)
        test_kyc_queue(admin)
        test_kyc_approve(admin, kyc)
        test_kyc_reject(admin, kyc)
        test_fraud_monitor(admin)
        test_audit_logs(admin)

    finally:
        print('\n→ Cleaning up test data...')
        cleanup()
        print('  Done.')

    print(f'\n{"=" * 60}')
    print(f'RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print(f'{"=" * 60}')

    if TESTS_FAILED:
        print('\n❌ ADMIN SMOKE TEST FAILED')
        sys.exit(1)
    else:
        print('\n✅ ADMIN SMOKE TEST PASSED')
        sys.exit(0)


if __name__ == '__main__':
    main()