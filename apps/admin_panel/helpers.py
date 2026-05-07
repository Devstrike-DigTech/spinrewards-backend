"""
Shared helpers for admin dashboard views.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework.pagination import PageNumberPagination


class AdminPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response_data(self, data):
        return {
            'count': self.page.paginator.count,
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            'results': data,
        }


def get_user_balance(user, balance_type: str) -> Decimal:
    try:
        from apps.wallet.services import WalletService
        return WalletService.get_balance(user, balance_type)
    except Exception:
        return Decimal('0')


def get_risk_level(user) -> str:
    """Low / Medium / High based on KYC status and account age."""
    try:
        kyc_status = user.kyc_profile.overall_status
    except Exception:
        kyc_status = 'unverified'

    if kyc_status == 'rejected':
        return 'High'
    if kyc_status in ('unverified', 'partial', 'pending'):
        return 'Medium'
    account_age_days = (timezone.now() - user.created_at).days
    if account_age_days < 7:
        return 'Medium'
    return 'Low'


def get_kyc_display(user) -> str:
    try:
        status = user.kyc_profile.overall_status
        return {
            'approved': 'Done',
            'pending': 'Pending',
            'partial': 'Pending',
            'unverified': 'Pending',
            'rejected': 'Rejected',
        }.get(status, status.capitalize())
    except Exception:
        return 'Pending'


def monthly_aggregation(queryset, date_field='created_at', value_field=None, months=12):
    """
    Returns monthly totals for the last N months as a list of dicts:
    [{'month': 'Jan', 'year': 2026, 'value': '12500.00'}, ...]
    """
    start = timezone.now() - timedelta(days=months * 31)
    qs = queryset.filter(**{f'{date_field}__gte': start})

    if value_field:
        qs = (
            qs.annotate(month=TruncMonth(date_field))
            .values('month')
            .annotate(total=Sum(value_field))
            .order_by('month')
        )
    else:
        qs = (
            qs.annotate(month=TruncMonth(date_field))
            .values('month')
            .annotate(total=Count('id'))
            .order_by('month')
        )

    result = []
    for row in qs:
        if row['month']:
            result.append({
                'month': row['month'].strftime('%b'),
                'year': row['month'].year,
                'value': str(row['total'] or 0),
            })
    return result


def format_user(user) -> str:
    """Build a display name from the Telegram user model fields."""
    parts = [
        getattr(user, 'first_name', '') or '',
        getattr(user, 'last_name', '') or '',
    ]
    name = ' '.join(p.strip() for p in parts if p.strip())
    return name or getattr(user, 'username', '') or f'User #{user.telegram_id}'


def pct_change(current: Decimal, previous: Decimal) -> str:
    if not previous or previous == 0:
        return '0'
    change = ((current - previous) / previous) * 100
    return str(round(change, 1))