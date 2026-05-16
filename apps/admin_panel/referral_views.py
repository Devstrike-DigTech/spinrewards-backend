"""
Admin Referral API Views.

Lives in apps/admin_panel/ to keep all admin-only logic together.

Endpoints:
  GET /api/v1/admin/referrals/    All referrals across the platform
"""
import logging

from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.admin_panel.helpers import AdminPagination, format_user
from apps.admin_panel.permissions import IsAdminUser
from apps.referrals.models import Referral

logger = logging.getLogger(__name__)


class AdminReferralsView(APIView):
    """
    GET /api/v1/admin/referrals/

    All referrals across the platform with overview stats.

    Query params:
      status    pending | qualified | rewarded | rejected
      search    referrer or referred user name
      page
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = Referral.objects.select_related(
            'referrer', 'referred_user', 'referral_code',
        ).order_by('-created_at')

        # Filters
        filter_status = request.query_params.get('status', '').strip()
        search        = request.query_params.get('search', '').strip()

        if filter_status:
            qs = qs.filter(status=filter_status)

        if search:
            qs = qs.filter(
                Q(referrer__first_name__icontains=search) |
                Q(referrer__last_name__icontains=search) |
                Q(referred_user__first_name__icontains=search) |
                Q(referred_user__last_name__icontains=search) |
                Q(referrer__username__icontains=search) |
                Q(referred_user__username__icontains=search)
            )

        # Overview stats
        total     = Referral.objects.count()
        pending   = Referral.objects.filter(status=Referral.Status.PENDING).count()
        qualified = Referral.objects.filter(status=Referral.Status.QUALIFIED).count()
        rewarded  = Referral.objects.filter(status=Referral.Status.REWARDED).count()

        paginator = AdminPagination()
        page = paginator.paginate_queryset(qs, request)

        results = []
        for r in page:
            results.append({
                'id': str(r.id),
                'referrer': {
                    'id': str(r.referrer.id),
                    'name': format_user(r.referrer),
                    'telegram_id': r.referrer.telegram_id,
                },
                'referred_user': {
                    'id': str(r.referred_user.id),
                    'name': format_user(r.referred_user),
                    'telegram_id': r.referred_user.telegram_id,
                },
                'code': r.referral_code.code if r.referral_code else '',
                'status': r.status,
                'qualified_at': (
                    r.qualified_at.isoformat() if r.qualified_at else None
                ),
                'rewarded_at': (
                    r.rewarded_at.isoformat() if r.rewarded_at else None
                ),
                'reward_snapshot': r.reward_snapshot,
                'created_at': r.created_at.isoformat(),
            })

        return Response({
            'success': True,
            'data': {
                'overview': {
                    'total': total,
                    'pending': pending,
                    'qualified': qualified,
                    'rewarded': rewarded,
                },
                'count': paginator.page.paginator.count,
                'next': paginator.get_next_link(),
                'previous': paginator.get_previous_link(),
                'results': results,
            },
        })