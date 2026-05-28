"""
AdminProfile model — admin credentials, role, and permissions.

This is the complete model. It stores:
  - Admin login credentials (email + hashed password), separate from Telegram users
  - The admin's role (super_admin, finance_admin, etc.)
  - A permission matrix mapping each role to allowed actions

The 5 roles (per spec §7.3):
  super_admin    Full access — RTP config, delete users, financial reports, admin management
  finance_admin  Withdrawals approval, deposit monitoring, financial reports only
  support_admin  View user profiles, freeze accounts, KYC review — no financial changes
  risk_admin     Fraud alerts, flag users, view audit logs — read-only financial
  read_only      View all dashboards — no actions permitted
"""
import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.db import models


class AdminProfile(models.Model):

    class Role(models.TextChoices):
        SUPER_ADMIN   = 'super_admin',   'Super Admin'
        FINANCE_ADMIN = 'finance_admin', 'Finance Admin'
        SUPPORT_ADMIN = 'support_admin', 'Support Admin'
        RISK_ADMIN    = 'risk_admin',    'Risk Admin'
        READ_ONLY     = 'read_only',     'Read Only'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        'users.User',
        on_delete=models.CASCADE,
        related_name='admin_profile',
    )
    email = models.EmailField(unique=True, db_index=True)
    hashed_password = models.CharField(max_length=256)
    display_name = models.CharField(max_length=100, blank=True)

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.READ_ONLY,
        db_index=True,
        help_text='Determines which admin actions are permitted.',
    )

    is_active = models.BooleanField(default=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'admin_profiles'

    def __str__(self):
        return f'AdminProfile({self.email}, role={self.role})'

    def set_password(self, raw_password: str):
        self.hashed_password = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password(raw_password, self.hashed_password)

    @property
    def is_super_admin(self) -> bool:
        return self.role == self.Role.SUPER_ADMIN

    def can(self, action: str) -> bool:
        """Return True if this admin's role permits the action."""
        return action in ROLE_PERMISSIONS.get(self.role, set())


# ─── Permission matrix ────────────────────────────────────────────────────────
# Maps each role to the set of actions it can perform.
# To change what a role can do, edit these sets.

VIEW_ALL = {
    'view_dashboard', 'view_financials', 'view_users', 'view_kyc',
    'view_withdrawals', 'view_fraud', 'view_audit_logs',
    'view_challenges', 'view_referrals', 'view_rtp',
}

ROLE_PERMISSIONS = {
    AdminProfile.Role.SUPER_ADMIN: VIEW_ALL | {
        'edit_rtp', 'create_rtp', 'delete_rtp',
        'create_challenge', 'edit_challenge', 'delete_challenge',
        'approve_withdrawal', 'reject_withdrawal',
        'approve_kyc', 'reject_kyc',
        'freeze_user', 'unfreeze_user', 'delete_user', 'flag_user', 'unflag_user',
        'manage_admins',
    },
    AdminProfile.Role.FINANCE_ADMIN: VIEW_ALL | {
        'approve_withdrawal', 'reject_withdrawal',
    },
    AdminProfile.Role.SUPPORT_ADMIN: VIEW_ALL | {
        'approve_kyc', 'reject_kyc',
        'freeze_user', 'unfreeze_user',
    },
    AdminProfile.Role.RISK_ADMIN: VIEW_ALL | {
        'flag_user', 'unflag_user',
    },
    AdminProfile.Role.READ_ONLY: VIEW_ALL,
}