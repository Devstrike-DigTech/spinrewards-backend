# """
# Admin auth views.

# POST /api/v1/admin/auth/login/    Email + password → JWT
# POST /api/v1/admin/auth/logout/   Blacklist refresh token
# GET  /api/v1/admin/auth/me/       Current admin user info
# POST /api/v1/admin/auth/change-password/  Change own password
# """
# import logging

# from django.utils import timezone
# from rest_framework import status
# from rest_framework.permissions import AllowAny, IsAuthenticated
# from rest_framework.response import Response
# from rest_framework.views import APIView
# from rest_framework_simplejwt.exceptions import TokenError
# from rest_framework_simplejwt.tokens import RefreshToken

# from apps.admin_panel.permissions import IsAdminUser

# from .models import AdminProfile

# logger = logging.getLogger(__name__)


# class AdminLoginView(APIView):
#     """
#     POST /api/v1/admin/auth/login/

#     Email + password login for admin dashboard.
#     Returns JWT access + refresh tokens (same format as Telegram auth).

#     Body:
#     {
#         "email": "admin@spinrewards.com",
#         "password": "secret123"
#     }

#     Response:
#     {
#         "success": true,
#         "data": {
#             "access_token": "eyJ...",
#             "refresh_token": "eyJ...",
#             "token_type": "Bearer",
#             "admin": {
#                 "id": "uuid",
#                 "email": "admin@spinrewards.com",
#                 "display_name": "John Admin",
#                 "is_staff": true
#             }
#         }
#     }
#     """
#     permission_classes = [AllowAny]

#     def post(self, request):
#         email = (request.data.get('email', '') or '').strip().lower()
#         password = request.data.get('password', '') or ''

#         if not email or not password:
#             return Response(
#                 {
#                     'error': True,
#                     'code': 'MISSING_CREDENTIALS',
#                     'message': 'Email and password are required.',
#                 },
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         # Look up the admin profile
#         try:
#             profile = AdminProfile.objects.select_related('user').get(
#                 email=email,
#                 is_active=True,
#             )
#         except AdminProfile.DoesNotExist:
#             logger.warning('Admin login failed: unknown email %s', email)
#             return Response(
#                 {
#                     'error': True,
#                     'code': 'INVALID_CREDENTIALS',
#                     'message': 'Invalid email or password.',
#                 },
#                 status=status.HTTP_401_UNAUTHORIZED,
#             )

#         # Verify password
#         if not profile.check_password(password):
#             logger.warning('Admin login failed: wrong password for %s', email)
#             return Response(
#                 {
#                     'error': True,
#                     'code': 'INVALID_CREDENTIALS',
#                     'message': 'Invalid email or password.',
#                 },
#                 status=status.HTTP_401_UNAUTHORIZED,
#             )

#         # Verify the linked User still has staff access
#         user = profile.user
#         if not user.is_active:
#             return Response(
#                 {
#                     'error': True,
#                     'code': 'ACCOUNT_DISABLED',
#                     'message': 'This admin account has been disabled.',
#                 },
#                 status=status.HTTP_403_FORBIDDEN,
#             )

#         if not (user.is_staff or user.is_superuser):
#             return Response(
#                 {
#                     'error': True,
#                     'code': 'NOT_ADMIN',
#                     'message': 'This account does not have admin access.',
#                 },
#                 status=status.HTTP_403_FORBIDDEN,
#             )

#         # Issue JWT tokens
#         refresh = RefreshToken.for_user(user)
#         access = refresh.access_token

#         # Record last login
#         profile.last_login_at = timezone.now()
#         profile.save(update_fields=['last_login_at'])

#         logger.info('Admin login: %s (user=%s)', email, user.id)

#         return Response(
#             {
#                 'success': True,
#                 'data': {
#                     'access_token': str(access),
#                     'refresh_token': str(refresh),
#                     'token_type': 'Bearer',
#                     'admin': {
#                         'id': str(user.id),
#                         'email': profile.email,
#                         'display_name': profile.display_name or _format_name(user),
#                         'role': profile.role,
#                         'role_label': profile.get_role_display(),
#                         'permissions': sorted(_get_permissions_for_role(profile.role)),
#                         'is_staff': user.is_staff,
#                         'is_superuser': user.is_superuser,
#                     },
#                 },
#             },
#             status=status.HTTP_200_OK,
#         )


# class AdminLogoutView(APIView):
#     """
#     POST /api/v1/admin/auth/logout/

#     Blacklists the refresh token so it can't be used again.

#     Body:
#     { "refresh_token": "eyJ..." }
#     """
#     permission_classes = [IsAuthenticated]

#     def post(self, request):
#         refresh_token = request.data.get('refresh_token', '').strip()

#         if not refresh_token:
#             return Response(
#                 {'error': True, 'code': 'MISSING_TOKEN', 'message': 'refresh_token is required.'},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         try:
#             RefreshToken(refresh_token).blacklist()
#         except TokenError:
#             # Already invalid or expired — treat as success
#             pass

#         logger.info('Admin logout: user=%s', request.user.id)

#         return Response(
#             {'success': True, 'data': {'message': 'Logged out successfully.'}},
#             status=status.HTTP_200_OK,
#         )


# class AdminMeView(APIView):
#     """
#     GET /api/v1/admin/auth/me/

#     Returns current admin user info.
#     Use to validate the token on dashboard load.
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request):
#         user = request.user

#         # Get admin profile if it exists
#         profile_data = {}
#         try:
#             profile = user.admin_profile
#             profile_data = {
#                 'email': profile.email,
#                 'display_name': profile.display_name or _format_name(user),
#                 'last_login_at': (
#                     profile.last_login_at.isoformat()
#                     if profile.last_login_at else None
#                 ),
#             }
#         except AdminProfile.DoesNotExist:
#             profile_data = {
#                 'email': '',
#                 'display_name': _format_name(user),
#                 'last_login_at': None,
#             }

#         return Response(
#             {
#                 'success': True,
#                 'data': {
#                     'id': str(user.id),
#                     'telegram_id': user.telegram_id,
#                     'is_staff': user.is_staff,
#                     'is_superuser': user.is_superuser,
#                     **profile_data,
#                 },
#             },
#             status=status.HTTP_200_OK,
#         )


# class AdminChangePasswordView(APIView):
#     """
#     POST /api/v1/admin/auth/change-password/

#     Change the current admin's own password.

#     Body:
#     {
#         "current_password": "old123",
#         "new_password": "new456"
#     }
#     """
#     permission_classes = [IsAdminUser]

#     def post(self, request):
#         current_password = request.data.get('current_password', '').strip()
#         new_password = request.data.get('new_password', '').strip()

#         if not current_password or not new_password:
#             return Response(
#                 {
#                     'error': True,
#                     'code': 'MISSING_FIELDS',
#                     'message': 'current_password and new_password are required.',
#                 },
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         if len(new_password) < 8:
#             return Response(
#                 {
#                     'error': True,
#                     'code': 'PASSWORD_TOO_SHORT',
#                     'message': 'New password must be at least 8 characters.',
#                 },
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         try:
#             profile = request.user.admin_profile
#         except AdminProfile.DoesNotExist:
#             return Response(
#                 {
#                     'error': True,
#                     'code': 'NO_ADMIN_PROFILE',
#                     'message': 'No admin profile found for this account.',
#                 },
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         if not profile.check_password(current_password):
#             return Response(
#                 {
#                     'error': True,
#                     'code': 'WRONG_PASSWORD',
#                     'message': 'Current password is incorrect.',
#                 },
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         profile.set_password(new_password)
#         profile.save(update_fields=['hashed_password', 'updated_at'])

#         logger.info('Admin password changed: user=%s', request.user.id)

#         return Response(
#             {'success': True, 'data': {'message': 'Password changed successfully.'}},
#             status=status.HTTP_200_OK,
#         )


# def _format_name(user) -> str:
#     parts = [
#         getattr(user, 'first_name', '') or '',
#         getattr(user, 'last_name', '') or '',
#     ]
#     name = ' '.join(p.strip() for p in parts if p.strip())
#     return name or getattr(user, 'username', '') or f'Admin #{user.telegram_id}'
"""
Admin auth views.

  POST /api/v1/admin/auth/login/             Email + password → JWT (+ role & permissions)
  POST /api/v1/admin/auth/logout/            Blacklist refresh token
  GET  /api/v1/admin/auth/me/                Current admin info (+ role & permissions)
  POST /api/v1/admin/auth/change-password/   Change own password
"""
import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .helpers import format_user
from .models import AdminProfile, ROLE_PERMISSIONS
from .permissions import IsAdminUser

logger = logging.getLogger(__name__)


def _permissions_for(role: str) -> list:
    return sorted(ROLE_PERMISSIONS.get(role, set()))


class AdminLoginView(APIView):
    """POST /api/v1/admin/auth/login/  — email + password → JWT."""
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get('email', '') or '').strip().lower()
        password = request.data.get('password', '') or ''

        if not email or not password:
            return Response(
                {'error': True, 'code': 'MISSING_CREDENTIALS',
                 'message': 'Email and password are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            profile = AdminProfile.objects.select_related('user').get(
                email=email, is_active=True,
            )
        except AdminProfile.DoesNotExist:
            logger.warning('Admin login failed: unknown email %s', email)
            return Response(
                {'error': True, 'code': 'INVALID_CREDENTIALS',
                 'message': 'Invalid email or password.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not profile.check_password(password):
            logger.warning('Admin login failed: wrong password for %s', email)
            return Response(
                {'error': True, 'code': 'INVALID_CREDENTIALS',
                 'message': 'Invalid email or password.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        user = profile.user
        if not user.is_active:
            return Response(
                {'error': True, 'code': 'ACCOUNT_DISABLED',
                 'message': 'This admin account has been disabled.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not (user.is_staff or user.is_superuser):
            return Response(
                {'error': True, 'code': 'NOT_ADMIN',
                 'message': 'This account does not have admin access.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = RefreshToken.for_user(user)
        access = refresh.access_token

        profile.last_login_at = timezone.now()
        profile.save(update_fields=['last_login_at'])

        logger.info('Admin login: %s (role=%s)', email, profile.role)

        return Response({
            'success': True,
            'data': {
                'access_token': str(access),
                'refresh_token': str(refresh),
                'token_type': 'Bearer',
                'admin': {
                    'id': str(user.id),
                    'email': profile.email,
                    'display_name': profile.display_name or format_user(user),
                    'role': profile.role,
                    'role_label': profile.get_role_display(),
                    'permissions': _permissions_for(profile.role),
                    'is_staff': user.is_staff,
                    'is_superuser': user.is_superuser,
                },
            },
        })


class AdminLogoutView(APIView):
    """POST /api/v1/admin/auth/logout/  — blacklist refresh token."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = (request.data.get('refresh_token', '') or '').strip()
        if not refresh_token:
            return Response(
                {'error': True, 'code': 'MISSING_TOKEN',
                 'message': 'refresh_token is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            pass
        return Response({'success': True, 'data': {'message': 'Logged out successfully.'}})


class AdminMeView(APIView):
    """GET /api/v1/admin/auth/me/  — current admin info. Validate token on load."""
    permission_classes = [IsAdminUser]

    def get(self, request):
        user = request.user
        profile = user.admin_profile  # guaranteed by IsAdminUser

        return Response({
            'success': True,
            'data': {
                'id': str(user.id),
                'telegram_id': user.telegram_id,
                'email': profile.email,
                'display_name': profile.display_name or format_user(user),
                'role': profile.role,
                'role_label': profile.get_role_display(),
                'permissions': _permissions_for(profile.role),
                'is_staff': user.is_staff,
                'is_superuser': user.is_superuser,
                'last_login_at': (
                    profile.last_login_at.isoformat() if profile.last_login_at else None
                ),
            },
        })


class AdminChangePasswordView(APIView):
    """POST /api/v1/admin/auth/change-password/  — change own password."""
    permission_classes = [IsAdminUser]

    def post(self, request):
        current_password = (request.data.get('current_password', '') or '').strip()
        new_password = (request.data.get('new_password', '') or '').strip()

        if not current_password or not new_password:
            return Response(
                {'error': True, 'code': 'MISSING_FIELDS',
                 'message': 'current_password and new_password are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(new_password) < 8:
            return Response(
                {'error': True, 'code': 'PASSWORD_TOO_SHORT',
                 'message': 'New password must be at least 8 characters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile = request.user.admin_profile
        if not profile.check_password(current_password):
            return Response(
                {'error': True, 'code': 'WRONG_PASSWORD',
                 'message': 'Current password is incorrect.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile.set_password(new_password)
        profile.save(update_fields=['hashed_password', 'updated_at'])

        return Response({'success': True, 'data': {'message': 'Password changed successfully.'}})