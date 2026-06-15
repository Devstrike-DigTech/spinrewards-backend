# """
# Admin dashboard permission.

# Only Django staff users (is_staff=True) or superusers can access these endpoints.
# Set is_staff=True on admin accounts via the Django admin or manage.py shell.
# """
# from rest_framework.permissions import BasePermission


# class IsAdminUser(BasePermission):
#     message = 'Admin access required.'

#     def has_permission(self, request, view):
#         return bool(
#             request.user
#             and request.user.is_authenticated
#             and (request.user.is_staff or request.user.is_superuser)
#         )
"""
Admin permission classes.

Use these as `permission_classes` on admin views.

  IsAdminUser            Any active admin (any role) — for view/dashboard endpoints
  IsSuperAdmin           super_admin only
  CanApproveWithdrawal   approve_withdrawal action
  CanRejectWithdrawal    reject_withdrawal action
  CanApproveKYC          approve_kyc action
  CanRejectKYC           reject_kyc action
  CanEditRTP             edit_rtp action
  CanCreateChallenge     create_challenge action
  CanEditChallenge       edit_challenge action
  CanDeleteChallenge     delete_challenge action
  CanFreezeUser          freeze_user action
  CanDeleteUser          delete_user action
  CanFlagUser            flag_user action
  CanManageAdmins        manage_admins action
"""
from rest_framework.permissions import BasePermission


def _active_profile(request):
    """Return the AdminProfile if the request user is an active admin, else None."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return None
    if not (user.is_staff or user.is_superuser):
        return None
    profile = getattr(user, 'admin_profile', None)
    if not profile or not profile.is_active:
        return None
    return profile


class IsAdminUser(BasePermission):
    """Any active admin (any role). Use on read-only / dashboard endpoints."""
    message = 'Admin access required.'

    def has_permission(self, request, view):
        return _active_profile(request) is not None


class HasAdminPermission(BasePermission):
    """Base class — subclass and set `required_action`."""
    required_action = None
    message = 'You do not have permission to perform this action.'

    def has_permission(self, request, view):
        profile = _active_profile(request)
        if profile is None:
            return False
        if not self.required_action:
            return True
        return profile.can(self.required_action)


class IsSuperAdmin(BasePermission):
    message = 'Super Admin access required.'

    def has_permission(self, request, view):
        profile = _active_profile(request)
        return bool(profile and profile.is_super_admin)


# ─── Action gates ─────────────────────────────────────────────────────────────

class CanApproveWithdrawal(HasAdminPermission):
    required_action = 'approve_withdrawal'
    message = 'You do not have permission to approve withdrawals.'


class CanRejectWithdrawal(HasAdminPermission):
    required_action = 'reject_withdrawal'
    message = 'You do not have permission to reject withdrawals.'


class CanApproveKYC(HasAdminPermission):
    required_action = 'approve_kyc'
    message = 'You do not have permission to approve KYC.'


class CanRejectKYC(HasAdminPermission):
    required_action = 'reject_kyc'
    message = 'You do not have permission to reject KYC.'


class CanEditRTP(HasAdminPermission):
    required_action = 'edit_rtp'
    message = 'You do not have permission to edit RTP settings.'

class CanViewSettings(HasAdminPermission):
    required_permission = 'view_settings'


class CanEditSettings(HasAdminPermission):
    required_permission = 'edit_settings'


class CanCreateChallenge(HasAdminPermission):
    required_action = 'create_challenge'
    message = 'You do not have permission to create challenges.'


class CanEditChallenge(HasAdminPermission):
    required_action = 'edit_challenge'
    message = 'You do not have permission to edit challenges.'


class CanDeleteChallenge(HasAdminPermission):
    required_action = 'delete_challenge'
    message = 'You do not have permission to delete challenges.'


class CanFreezeUser(HasAdminPermission):
    required_action = 'freeze_user'
    message = 'You do not have permission to freeze users.'


class CanDeleteUser(HasAdminPermission):
    required_action = 'delete_user'
    message = 'Only Super Admins can delete users.'


class CanFlagUser(HasAdminPermission):
    required_action = 'flag_user'
    message = 'You do not have permission to flag users.'


class CanManageAdmins(HasAdminPermission):
    required_action = 'manage_admins'
    message = 'Only Super Admins can manage other admins.'