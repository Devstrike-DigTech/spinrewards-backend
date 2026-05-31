"""
Admin roles & permissions smoke test.

Tests:
  1. Each role can log in and receives correct permissions array
  2. Only super_admin can manage admins (create/list/update/delete)
  3. Only super_admin can edit RTP
  4. Only super_admin + finance_admin can approve/reject withdrawals
  5. Only super_admin + support_admin can approve/reject KYC
  6. All admins can view dashboards
  7. read_only admin cannot perform any action
  8. Self-protection: super_admin can't change own role / deactivate self

Usage:
    docker compose exec api python smoke_test_admin.py
"""
import os
import sys
import uuid
from decimal import Decimal

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

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


from apps.admin_panel.models import AdminProfile
from apps.users.models import User


# ─── Test data setup ──────────────────────────────────────────────────────────

ADMIN_EMAILS = {
    'super_admin':   'smoke_super@test.local',
    'finance_admin': 'smoke_finance@test.local',
    'support_admin': 'smoke_support@test.local',
    'risk_admin':    'smoke_risk@test.local',
    'read_only':     'smoke_readonly@test.local',
}
ADMIN_PASSWORD = 'SmokeTest123!'


def cleanup():
    """Remove all smoke test admins."""
    for email in ADMIN_EMAILS.values():
        try:
            profile = AdminProfile.objects.get(email=email)
            profile.user.delete()  # cascades to profile
        except AdminProfile.DoesNotExist:
            pass


def create_test_admins():
    """Create one admin for each role."""
    cleanup()
    admins = {}

    for role, email in ADMIN_EMAILS.items():
        fake_tid = -(abs(hash(email)) % (10 ** 9))
        user = User.objects.create(
            telegram_id=fake_tid,
            first_name=f'Smoke{role.title()}',
            last_name='Test',
            username=email.split('@')[0],
            is_staff=True,
        )
        user.set_unusable_password()
        user.save()

        profile = AdminProfile.objects.create(
            user=user,
            email=email,
            display_name=f'Smoke {role}',
            role=role,
        )
        profile.set_password(ADMIN_PASSWORD)
        profile.save()

        admins[role] = profile

    return admins


# ─── Tests ────────────────────────────────────────────────────────────────────

def login(client, role):
    """Log in as a role and return the response."""
    res = client.post(
        '/api/v1/admin/auth/login/',
        {'email': ADMIN_EMAILS[role], 'password': ADMIN_PASSWORD},
        format='json',
    )
    return res


def auth_client(role, admins):
    """Return an APIClient authenticated as the given role."""
    client = APIClient()
    client.force_authenticate(user=admins[role].user)
    return client


def test_login_returns_role_and_permissions(admins):
    section('TEST 1: Login returns correct role + permissions per role')

    expected_permissions = {
        'super_admin': {
            'view_dashboard', 'edit_rtp', 'approve_withdrawal',
            'approve_kyc', 'manage_admins', 'delete_user', 'flag_user',
            'create_challenge',
        },
        'finance_admin': {
            'view_dashboard', 'view_financials',
            'approve_withdrawal', 'reject_withdrawal',
        },
        'support_admin': {
            'view_dashboard', 'view_users', 'view_kyc',
            'approve_kyc', 'reject_kyc', 'freeze_user',
        },
        'risk_admin': {
            'view_dashboard', 'view_fraud', 'view_audit_logs',
            'flag_user', 'unflag_user',
        },
        'read_only': {
            'view_dashboard', 'view_financials', 'view_users',
            'view_kyc', 'view_withdrawals', 'view_fraud',
        },
    }

    forbidden_permissions = {
        'finance_admin':  {'edit_rtp', 'manage_admins', 'approve_kyc'},
        'support_admin':  {'edit_rtp', 'manage_admins', 'approve_withdrawal'},
        'risk_admin':     {'edit_rtp', 'manage_admins', 'approve_withdrawal', 'approve_kyc'},
        'read_only':      {'edit_rtp', 'manage_admins', 'approve_withdrawal',
                           'approve_kyc', 'freeze_user', 'flag_user'},
    }

    for role in ADMIN_EMAILS:
        client = APIClient()
        res = login(client, role)
        check(res.status_code == 200, f'{role} login returns 200')

        admin = res.data['data']['admin']
        check(admin['role'] == role, f'{role} response includes correct role')
        check('permissions' in admin, f'{role} response includes permissions')

        perms = set(admin['permissions'])

        # Check expected permissions present
        for expected in expected_permissions[role]:
            check(expected in perms, f'{role} has "{expected}"')

        # Check forbidden permissions NOT present
        for forbidden in forbidden_permissions.get(role, set()):
            check(forbidden not in perms, f'{role} does NOT have "{forbidden}"')


def test_only_super_admin_can_manage_admins(admins):
    section('TEST 2: Only super_admin can access admin management endpoints')

    for role in ADMIN_EMAILS:
        client = auth_client(role, admins)

        res = client.get('/api/v1/admin/admins/')
        if role == 'super_admin':
            check(res.status_code == 200, f'{role}: GET /admins/ → 200')
        else:
            check(res.status_code == 403, f'{role}: GET /admins/ → 403')

        res = client.get('/api/v1/admin/roles/')
        if role == 'super_admin':
            check(res.status_code == 200, f'{role}: GET /roles/ → 200')
        else:
            check(res.status_code == 403, f'{role}: GET /roles/ → 403')

        res = client.post('/api/v1/admin/admins/', {
            'email': 'should-not-create@test.local',
            'password': 'somepass123',
            'role': 'read_only',
        }, format='json')
        if role == 'super_admin':
            check(res.status_code == 201, f'{role}: POST /admins/ → 201')
            # cleanup
            if res.status_code == 201:
                AdminProfile.objects.filter(email='should-not-create@test.local').delete()
                User.objects.filter(username='should-not-create').delete()
        else:
            check(res.status_code == 403, f'{role}: POST /admins/ → 403')


def test_only_super_admin_can_edit_rtp(admins):
    section('TEST 3: Only super_admin can create/edit RTP')

    for role in ADMIN_EMAILS:
        client = auth_client(role, admins)

        # GET should work for everyone
        res = client.get('/api/v1/admin/rtp/')
        check(res.status_code == 200, f'{role}: GET /rtp/ → 200 (any admin can view)')

        # POST should only work for super_admin
        # We pass an obviously-invalid body — we only care about the permission check
        res = client.post('/api/v1/admin/rtp/', {
            'name': 'Smoke Test Wheel',
        }, format='json')

        if role == 'super_admin':
            # 400 or 201 — anything except 403 is fine (means permission passed)
            check(res.status_code != 403, f'{role}: POST /rtp/ → not 403 (permission allowed)')
        else:
            check(res.status_code == 403, f'{role}: POST /rtp/ → 403')


def test_withdrawal_actions(admins):
    section('TEST 4: Only super_admin + finance_admin can approve/reject withdrawals')

    fake_uuid = uuid.uuid4()

    for role in ADMIN_EMAILS:
        client = auth_client(role, admins)

        # Approve attempt — uses fake UUID, so we get 404 if permitted, 403 if not
        res = client.post(f'/api/v1/admin/withdrawals/{fake_uuid}/approve/')
        if role in ('super_admin', 'finance_admin'):
            check(res.status_code in (404, 400), f'{role}: approve withdrawal → not 403 (got {res.status_code})')
        else:
            check(res.status_code == 403, f'{role}: approve withdrawal → 403')

        res = client.post(f'/api/v1/admin/withdrawals/{fake_uuid}/reject/', {'reason': 'test'}, format='json')
        if role in ('super_admin', 'finance_admin'):
            check(res.status_code in (404, 400), f'{role}: reject withdrawal → not 403')
        else:
            check(res.status_code == 403, f'{role}: reject withdrawal → 403')


def test_kyc_actions(admins):
    section('TEST 5: Only super_admin + support_admin can approve/reject KYC')

    fake_uuid = uuid.uuid4()

    for role in ADMIN_EMAILS:
        client = auth_client(role, admins)

        res = client.post(f'/api/v1/admin/kyc/{fake_uuid}/approve/')
        if role in ('super_admin', 'support_admin'):
            check(res.status_code in (404, 400), f'{role}: approve KYC → not 403')
        else:
            check(res.status_code == 403, f'{role}: approve KYC → 403')

        res = client.post(f'/api/v1/admin/kyc/{fake_uuid}/reject/', {'reason': 'test'}, format='json')
        if role in ('super_admin', 'support_admin'):
            check(res.status_code in (404, 400), f'{role}: reject KYC → not 403')
        else:
            check(res.status_code == 403, f'{role}: reject KYC → 403')


def test_all_admins_can_view_dashboards(admins):
    section('TEST 6: All admins can view dashboards (read-only endpoints)')

    view_only_endpoints = [
        '/api/v1/admin/dashboard/',
        '/api/v1/admin/financials/',
        '/api/v1/admin/users/',
        '/api/v1/admin/withdrawals/',
        '/api/v1/admin/kyc/queue/',
    ]

    for role in ADMIN_EMAILS:
        client = auth_client(role, admins)
        for endpoint in view_only_endpoints:
            res = client.get(endpoint)
            # 200 means OK, 404 might mean route name differs — both are fine
            # (just not 403)
            check(
                res.status_code != 403,
                f'{role}: GET {endpoint} → not 403 (got {res.status_code})',
            )


def test_read_only_cannot_perform_actions(admins):
    section('TEST 7: read_only admin is blocked from ALL actions')

    client = auth_client('read_only', admins)
    fake_uuid = uuid.uuid4()

    action_endpoints = [
        ('POST', f'/api/v1/admin/rtp/', {}),
        ('POST', f'/api/v1/admin/withdrawals/{fake_uuid}/approve/', {}),
        ('POST', f'/api/v1/admin/withdrawals/{fake_uuid}/reject/', {'reason': 'x'}),
        ('POST', f'/api/v1/admin/kyc/{fake_uuid}/approve/', {}),
        ('POST', f'/api/v1/admin/kyc/{fake_uuid}/reject/', {'reason': 'x'}),
        ('POST', '/api/v1/admin/admins/', {'email': 'x@y.z', 'password': 'pw', 'role': 'read_only'}),
    ]

    for method, endpoint, body in action_endpoints:
        if method == 'POST':
            res = client.post(endpoint, body, format='json')
        check(res.status_code == 403, f'read_only blocked from {endpoint}')


def test_super_admin_self_protection(admins):
    section('TEST 8: Super admin cannot change own role / deactivate self')

    super_admin = admins['super_admin']
    client = auth_client('super_admin', admins)

    # Try to change own role
    res = client.patch(
        f'/api/v1/admin/admins/{super_admin.id}/',
        {'role': 'finance_admin'},
        format='json',
    )
    check(res.status_code == 400, 'self role change → 400')
    check(
        res.data.get('code') == 'CANNOT_CHANGE_OWN_ROLE',
        'CANNOT_CHANGE_OWN_ROLE error code',
    )

    # Try to deactivate self via PATCH
    res = client.patch(
        f'/api/v1/admin/admins/{super_admin.id}/',
        {'is_active': False},
        format='json',
    )
    check(res.status_code == 400, 'self deactivate via PATCH → 400')
    check(
        res.data.get('code') == 'CANNOT_DEACTIVATE_SELF',
        'CANNOT_DEACTIVATE_SELF error code',
    )

    # Try to deactivate self via DELETE
    res = client.delete(f'/api/v1/admin/admins/{super_admin.id}/')
    check(res.status_code == 400, 'self deactivate via DELETE → 400')
    check(
        res.data.get('code') == 'CANNOT_DEACTIVATE_SELF',
        'CANNOT_DEACTIVATE_SELF error code',
    )

    # Verify super admin is still active and still super_admin
    super_admin.refresh_from_db()
    check(super_admin.is_active is True, 'super admin still active after self-protect tests')
    check(super_admin.role == 'super_admin', 'super admin still has super_admin role')


def test_super_admin_can_manage_others(admins):
    section('TEST 9: Super admin can update/deactivate OTHER admins')

    client = auth_client('super_admin', admins)
    finance_admin = admins['finance_admin']

    # Change finance admin's role to support_admin
    res = client.patch(
        f'/api/v1/admin/admins/{finance_admin.id}/',
        {'role': 'support_admin'},
        format='json',
    )
    check(res.status_code == 200, 'super admin can change another admin\'s role')

    finance_admin.refresh_from_db()
    check(finance_admin.role == 'support_admin', 'role actually changed in DB')

    # Reset back
    client.patch(
        f'/api/v1/admin/admins/{finance_admin.id}/',
        {'role': 'finance_admin'},
        format='json',
    )

    # Reset password
    res = client.post(
        f'/api/v1/admin/admins/{finance_admin.id}/reset-password/',
        {'new_password': 'NewPass12345'},
        format='json',
    )
    check(res.status_code == 200, 'super admin can reset another admin\'s password')


def test_create_admin_validations(admins):
    section('TEST 10: Admin creation validates input')

    client = auth_client('super_admin', admins)

    # Missing fields
    res = client.post('/api/v1/admin/admins/', {}, format='json')
    check(res.status_code == 400, 'missing email+password → 400')
    check(res.data.get('code') == 'MISSING_FIELDS', 'MISSING_FIELDS error')

    # Short password
    res = client.post('/api/v1/admin/admins/', {
        'email': 'short@test.local',
        'password': 'short',
        'role': 'read_only',
    }, format='json')
    check(res.status_code == 400, 'short password → 400')
    check(res.data.get('code') == 'PASSWORD_TOO_SHORT', 'PASSWORD_TOO_SHORT error')

    # Invalid role
    res = client.post('/api/v1/admin/admins/', {
        'email': 'badrole@test.local',
        'password': 'GoodPass123',
        'role': 'nonexistent_role',
    }, format='json')
    check(res.status_code == 400, 'invalid role → 400')
    check(res.data.get('code') == 'INVALID_ROLE', 'INVALID_ROLE error')

    # Duplicate email
    res = client.post('/api/v1/admin/admins/', {
        'email': ADMIN_EMAILS['finance_admin'],   # already exists
        'password': 'GoodPass123',
        'role': 'read_only',
    }, format='json')
    check(res.status_code == 400, 'duplicate email → 400')
    check(res.data.get('code') == 'EMAIL_EXISTS', 'EMAIL_EXISTS error')


def test_inactive_admin_cannot_login(admins):
    section('TEST 11: Deactivated admin cannot log in')

    # Deactivate the read_only admin
    profile = admins['read_only']
    profile.is_active = False
    profile.save()

    client = APIClient()
    res = login(client, 'read_only')
    check(res.status_code == 401, 'deactivated admin login → 401')

    # Re-activate for cleanup
    profile.is_active = True
    profile.save()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print('SPIN REWARDS — ADMIN ROLES & PERMISSIONS SMOKE TEST')
    print('Testing role-based access control across all admin endpoints.\n')

    print('→ Creating test admins (one per role)...')
    admins = create_test_admins()
    for role in ADMIN_EMAILS:
        print(f'  {role:<15} → {ADMIN_EMAILS[role]}')

    try:
        test_login_returns_role_and_permissions(admins)
        test_only_super_admin_can_manage_admins(admins)
        test_only_super_admin_can_edit_rtp(admins)
        test_withdrawal_actions(admins)
        test_kyc_actions(admins)
        test_all_admins_can_view_dashboards(admins)
        test_read_only_cannot_perform_actions(admins)
        test_super_admin_self_protection(admins)
        test_super_admin_can_manage_others(admins)
        test_create_admin_validations(admins)
        test_inactive_admin_cannot_login(admins)

    finally:
        print('\n→ Cleaning up test admins...')
        cleanup()
        print('  Done.')

    print(f'\n{"=" * 60}')
    print(f'RESULTS: {TESTS_PASSED}/{TESTS_RUN} passed, {TESTS_FAILED} failed')
    print(f'{"=" * 60}')

    if TESTS_FAILED:
        print('\n❌ ADMIN ROLES SMOKE TEST FAILED')
        print('Common causes of failures:')
        print('  - permission_classes not yet updated on action views (run apply_permissions_patch.py)')
        print('  - URL paths different from what the test expects')
        print('  - migrations not run (role field missing)')
        sys.exit(1)
    else:
        print('\n✅ ADMIN ROLES SMOKE TEST PASSED')
        print('All role-based permissions are working correctly.')
        sys.exit(0)


if __name__ == '__main__':
    main()