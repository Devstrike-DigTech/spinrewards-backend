"""
Admin endpoints for managing system settings.

  GET   /api/v1/admin/settings/             List all settings
  PATCH /api/v1/admin/settings/<key>/       Update a single setting

Permission: super_admin only (uses CanEditRTP since it's a similar
revenue-impacting action). Could be a new CanEditSettings permission
if you want tighter granularity later.
"""
import logging
from decimal import Decimal, InvalidOperation

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_panel.permissions import CanEditRTP

from .models import SettingKey
from .services import get_all_settings, set_setting

logger = logging.getLogger(__name__)


class AdminSettingsListView(APIView):
    """GET /api/v1/admin/settings/  — list all settings + current values."""
    permission_classes = [CanEditRTP]

    def get(self, request):
        return Response({
            'success': True,
            'data': {'settings': get_all_settings()},
        })


class AdminSettingDetailView(APIView):
    """
    PATCH /api/v1/admin/settings/<key>/  — update a single setting.

    Body: { "value": "1500" }
    """
    permission_classes = [CanEditRTP]

    def patch(self, request, key):
        if key not in SettingKey.DEFAULTS:
            return Response(
                {'error': True, 'code': 'UNKNOWN_SETTING',
                 'message': f'Unknown setting key: {key}'},
                status=status.HTTP_404_NOT_FOUND,
            )

        raw_value = request.data.get('value')
        if raw_value is None:
            return Response(
                {'error': True, 'code': 'MISSING_VALUE',
                 'message': 'value is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, TypeError):
            return Response(
                {'error': True, 'code': 'INVALID_VALUE',
                 'message': 'value must be a number.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if value < 0:
            return Response(
                {'error': True, 'code': 'INVALID_VALUE',
                 'message': 'value must be non-negative.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        set_setting(key, value, updated_by=request.user)
        logger.info('Setting %s updated to %s by %s', key, value, request.user.id)

        return Response({
            'success': True,
            'message': f'{key} updated.',
            'data': {'key': key, 'value': str(value)},
        })
