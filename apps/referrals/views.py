"""
Referral player API views.

Admin endpoints live in apps/admin_panel/referral_views.py.

Player endpoints (Telegram JWT required):
  GET  /api/v1/referrals/my-code/        Get my referral code
  POST /api/v1/referrals/apply/          Apply a referral code
  GET  /api/v1/referrals/my-referrals/   My referral history
"""
import logging

from django.conf import settings
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Referral
from .services import ReferralService, ReferralServiceError

logger = logging.getLogger(__name__)


class Pagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


def format_user(user) -> str:
    """Build a display name from User fields. Used by player + admin views."""
    parts = [
        getattr(user, 'first_name', '') or '',
        getattr(user, 'last_name', '') or '',
    ]
    name = ' '.join(x for x in parts if x)
    return name or getattr(user, 'username', '') or f'User #{user.telegram_id}'


def serialize_referral(referral: Referral) -> dict:
    return {
        'id': str(referral.id),
        'referred_user': {
            'id': str(referral.referred_user.id),
            'name': format_user(referral.referred_user),
            'telegram_id': referral.referred_user.telegram_id,
        },
        'status': referral.status,
        'qualified_at': (
            referral.qualified_at.isoformat() if referral.qualified_at else None
        ),
        'rewarded_at': (
            referral.rewarded_at.isoformat() if referral.rewarded_at else None
        ),
        'created_at': referral.created_at.isoformat(),
    }


class MyReferralCodeView(APIView):
    """
    GET /api/v1/referrals/my-code/

    Returns the user's unique referral code.
    Creates one if it doesn't exist yet (lazy generation).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        code = ReferralService.get_or_create_code(request.user)
        stats = ReferralService.get_referral_stats(request.user)

        # Telegram deep-link — uses TELEGRAM_BOT_USERNAME from settings if available
        bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'SpinRewardsBot')
        share_url = f'https://t.me/{bot_username}?start={code.code}'

        return Response({
            'success': True,
            'data': {
                'code': code.code,
                'share_url': share_url,
                'stats': stats,
            },
        })


class ApplyReferralCodeView(APIView):
    """
    POST /api/v1/referrals/apply/

    Apply a referral code. Call after registration, before first deposit.

    Body: { "code": "SPIN-X7K2M9PQ" }
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        code_str = (request.data.get('code', '') or '').strip()

        if not code_str:
            return Response(
                {'error': True, 'code': 'MISSING_CODE',
                 'message': 'Referral code is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            referral = ReferralService.apply_code(request.user, code_str)
        except ReferralServiceError as e:
            return Response(
                {'error': True, 'code': 'INVALID_CODE', 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            'success': True,
            'data': {
                'message': 'Referral code applied successfully.',
                'referral_id': str(referral.id),
                'referrer_name': format_user(referral.referrer),
            },
        })


class MyReferralsView(APIView):
    """
    GET /api/v1/referrals/my-referrals/

    List all users the current user has referred + stats.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        referrals = Referral.objects.filter(
            referrer=request.user,
        ).select_related('referred_user').order_by('-created_at')

        stats = ReferralService.get_referral_stats(request.user)

        paginator = Pagination()
        page = paginator.paginate_queryset(referrals, request)

        return Response({
            'success': True,
            'data': {
                'stats': stats,
                'count': paginator.page.paginator.count,
                'next': paginator.get_next_link(),
                'previous': paginator.get_previous_link(),
                'referrals': [serialize_referral(r) for r in page],
            },
        })