"""
Admin Management views — Super Admin manages other admins.

  GET    /api/v1/admin/roles/                          List roles + permissions
  GET    /api/v1/admin/admins/                          List all admins
  POST   /api/v1/admin/admins/                          Create a new admin
  GET    /api/v1/admin/admins/<uuid>/                   Get one admin
  PATCH  /api/v1/admin/admins/<uuid>/                   Update role / name / active
  DELETE /api/v1/admin/admins/<uuid>/                   Deactivate admin
  POST   /api/v1/admin/admins/<uuid>/reset-password/    Reset password

All require the manage_admins permission (super_admin only).
"""
import logging

from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .helpers import AdminPagination, format_user
from .models import AdminProfile, ROLE_PERMISSIONS
from .permissions import CanManageAdmins

logger = logging.getLogger(__name__)


def _serialize_admin(profile: AdminProfile) -> dict:
    return {
        'id': str(profile.id),
        'user_id': str(profile.user_id),
        'email': profile.email,
        'display_name': profile.display_name or format_user(profile.user),
        'role': profile.role,
        'role_label': profile.get_role_display(),
        'permissions': sorted(ROLE_PERMISSIONS.get(profile.role, set())),
        'is_active': profile.is_active,
        'last_login_at': (
            profile.last_login_at.isoformat() if profile.last_login_at else None
        ),
        'created_at': profile.created_at.isoformat(),
    }


class AdminRolesView(APIView):
    """GET /api/v1/admin/roles/  — roles + their permissions (for dropdowns)."""
    permission_classes = [CanManageAdmins]

    def get(self, request):
        roles = [
            {
                'value': value,
                'label': label,
                'permissions': sorted(ROLE_PERMISSIONS.get(value, set())),
            }
            for value, label in AdminProfile.Role.choices
        ]
        return Response({'success': True, 'data': {'roles': roles}})


class AdminListCreateView(APIView):
    """
    GET  /api/v1/admin/admins/    List all admins (filters: role, is_active, search)
    POST /api/v1/admin/admins/    Create a new admin
    """
    permission_classes = [CanManageAdmins]

    def get(self, request):
        qs = AdminProfile.objects.select_related('user').all().order_by('-created_at')

        role_filter   = request.query_params.get('role', '').strip()
        active_filter = request.query_params.get('is_active', '').strip()
        search        = request.query_params.get('search', '').strip()

        if role_filter:
            qs = qs.filter(role=role_filter)
        if active_filter in ('true', 'false'):
            qs = qs.filter(is_active=(active_filter == 'true'))
        if search:
            qs = qs.filter(Q(email__icontains=search) | Q(display_name__icontains=search))

        paginator = AdminPagination()
        page = paginator.paginate_queryset(qs, request)

        return Response({
            'success': True,
            'data': {
                'count': paginator.page.paginator.count,
                'next': paginator.get_next_link(),
                'previous': paginator.get_previous_link(),
                'results': [_serialize_admin(p) for p in page],
            },
        })

    def post(self, request):
        from apps.users.models import User

        email        = (request.data.get('email', '') or '').strip().lower()
        password     = request.data.get('password', '') or ''
        display_name = (request.data.get('display_name', '') or '').strip()
        role         = (request.data.get('role', '') or '').strip()

        if not email or not password:
            return Response(
                {'error': True, 'code': 'MISSING_FIELDS',
                 'message': 'email and password are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(password) < 8:
            return Response(
                {'error': True, 'code': 'PASSWORD_TOO_SHORT',
                 'message': 'Password must be at least 8 characters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        valid_roles = [r.value for r in AdminProfile.Role]
        if role not in valid_roles:
            return Response(
                {'error': True, 'code': 'INVALID_ROLE',
                 'message': f'role must be one of: {valid_roles}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if AdminProfile.objects.filter(email=email).exists():
            return Response(
                {'error': True, 'code': 'EMAIL_EXISTS',
                 'message': 'An admin with this email already exists.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            fake_tid = -(abs(hash(email)) % (10 ** 9))
            first = display_name.split()[0] if display_name else 'Admin'
            last = ' '.join(display_name.split()[1:]) if display_name else ''

            user, _ = User.objects.get_or_create(
                telegram_id=fake_tid,
                defaults={
                    'first_name': first,
                    'last_name': last,
                    'username': email.split('@')[0],
                    'is_staff': True,
                },
            )
            if not user.is_staff:
                user.is_staff = True
                user.save(update_fields=['is_staff'])

            profile = AdminProfile.objects.create(
                user=user, email=email, display_name=display_name, role=role,
            )
            profile.set_password(password)
            profile.save()

        logger.info('Admin created: %s (role=%s) by %s', email, role, request.user.id)

        return Response(
            {'success': True, 'message': 'Admin created successfully.',
             'data': _serialize_admin(profile)},
            status=status.HTTP_201_CREATED,
        )


class AdminDetailView(APIView):
    """
    GET    /api/v1/admin/admins/<uuid>/    Get one
    PATCH  /api/v1/admin/admins/<uuid>/    Update role / name / active
    DELETE /api/v1/admin/admins/<uuid>/    Deactivate
    """
    permission_classes = [CanManageAdmins]

    def _get(self, admin_id):
        try:
            return AdminProfile.objects.select_related('user').get(id=admin_id)
        except AdminProfile.DoesNotExist:
            return None

    def get(self, request, admin_id):
        profile = self._get(admin_id)
        if not profile:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Admin not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({'success': True, 'data': _serialize_admin(profile)})

    def patch(self, request, admin_id):
        profile = self._get(admin_id)
        if not profile:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Admin not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Self-protection
        if profile.user_id == request.user.id:
            if 'role' in request.data and request.data['role'] != profile.role:
                return Response(
                    {'error': True, 'code': 'CANNOT_CHANGE_OWN_ROLE',
                     'message': 'You cannot change your own role.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if request.data.get('is_active') is False:
                return Response(
                    {'error': True, 'code': 'CANNOT_DEACTIVATE_SELF',
                     'message': 'You cannot deactivate your own account.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        body = request.data
        if 'display_name' in body:
            profile.display_name = body['display_name']
        if 'role' in body:
            valid_roles = [r.value for r in AdminProfile.Role]
            if body['role'] not in valid_roles:
                return Response(
                    {'error': True, 'code': 'INVALID_ROLE',
                     'message': f'role must be one of: {valid_roles}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            profile.role = body['role']
        if 'is_active' in body:
            profile.is_active = bool(body['is_active'])

        profile.save()
        logger.info('Admin updated: %s by %s', profile.email, request.user.id)

        return Response({
            'success': True, 'message': 'Admin updated.',
            'data': _serialize_admin(profile),
        })

    def delete(self, request, admin_id):
        profile = self._get(admin_id)
        if not profile:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Admin not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if profile.user_id == request.user.id:
            return Response(
                {'error': True, 'code': 'CANNOT_DEACTIVATE_SELF',
                 'message': 'You cannot deactivate your own account.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile.is_active = False
        profile.save(update_fields=['is_active', 'updated_at'])
        logger.info('Admin deactivated: %s by %s', profile.email, request.user.id)
        return Response({'success': True, 'message': 'Admin deactivated.'})


class AdminResetPasswordView(APIView):
    """POST /api/v1/admin/admins/<uuid>/reset-password/  — reset an admin's password."""
    permission_classes = [CanManageAdmins]

    def post(self, request, admin_id):
        try:
            profile = AdminProfile.objects.get(id=admin_id)
        except AdminProfile.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Admin not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        new_password = request.data.get('new_password', '') or ''
        if len(new_password) < 8:
            return Response(
                {'error': True, 'code': 'PASSWORD_TOO_SHORT',
                 'message': 'Password must be at least 8 characters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile.set_password(new_password)
        profile.save(update_fields=['hashed_password', 'updated_at'])
        logger.info('Admin password reset: %s by %s', profile.email, request.user.id)
        return Response({'success': True, 'message': f'Password reset for {profile.email}.'})