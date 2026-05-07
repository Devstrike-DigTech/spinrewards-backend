"""
Admin dashboard permission.

Only Django staff users (is_staff=True) or superusers can access these endpoints.
Set is_staff=True on admin accounts via the Django admin or manage.py shell.
"""
from rest_framework.permissions import BasePermission


class IsAdminUser(BasePermission):
    message = 'Admin access required.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_staff or request.user.is_superuser)
        )