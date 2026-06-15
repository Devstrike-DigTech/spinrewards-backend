"""
Admin Dashboard API Views.

All endpoints require IsAdminUser (is_staff=True or is_superuser=True).

Endpoints:
  GET  /api/v1/admin/dashboard/                    Screen 1 — Dashboard
  GET  /api/v1/admin/financials/                   Screen 2 — Financials
  GET  /api/v1/admin/rtp/                          Screen 3 — RTP Control list
  POST /api/v1/admin/rtp/                          Screen 3 — Create wheel config
  PUT  /api/v1/admin/rtp/<wheel_id>/               Screen 3 — Edit wheel config
  GET  /api/v1/admin/users/                        Screen 4 — Users list
  GET  /api/v1/admin/users/<user_id>/              Screen 4 — User detail
  GET  /api/v1/admin/users/<user_id>/spins/        Screen 4 — User recent spins
  GET  /api/v1/admin/users/<user_id>/transactions/ Screen 4 — User transactions
  GET  /api/v1/admin/withdrawals/                  Screen 5 — Withdrawal Management
  POST /api/v1/admin/withdrawals/<id>/approve/     Screen 5 — Approve
  POST /api/v1/admin/withdrawals/<id>/reject/      Screen 5 — Reject
  GET  /api/v1/admin/kyc/queue/                    Screen 6 — KYC Review Queue
  POST /api/v1/admin/kyc/<id>/approve/             Screen 6 — Approve KYC section
  POST /api/v1/admin/kyc/<id>/reject/              Screen 6 — Reject KYC section
  GET  /api/v1/admin/fraud/                        Screen 7 — Fraud & Risk Monitor
  GET  /api/v1/admin/audit-logs/                   Screen 8 — Audit Logs
"""
import logging
from datetime import timedelta
from decimal import Decimal
import profile

from django.contrib.auth import get_user_model
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.services import NotificationService
from apps.wallet.services import WalletService

from .helpers import (
    AdminPagination,
    format_user,
    get_kyc_display,
    get_risk_level,
    get_user_balance,
    monthly_aggregation,
    pct_change,
)
from .permissions import CanApproveKYC, CanApproveWithdrawal, CanEditRTP, CanRejectKYC, CanRejectWithdrawal, IsAdminUser

logger = logging.getLogger(__name__)
User = get_user_model()


# ══════════════════════════════════════════════════════════════════════════════
# SCREEN 1 — DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

class DashboardView(APIView):
    """
    GET /api/v1/admin/dashboard/

    Query params:
      year      4-digit year e.g. 2026 (default: current year)
      month     2-digit month e.g. 05 (only valid with year)

    Behavior:
      ?year=2026          → monthly data for all 12 months of 2026
      ?year=2026&month=05 → daily data for every day in May 2026
      (no params)         → monthly data for current year

    Percentage change compares selected year vs previous year
    (or selected month vs same month last year).
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        from apps.spin.models import Spin
        from apps.wallet.models import Transaction
        from apps.withdrawals.models import Withdrawal
        from django.db.models.functions import TruncDay, TruncMonth

        MONTH_NAMES_LIST = [
            '', 'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December',
        ]

        now = timezone.now()


        # ── Parse year + month params ─────────────────────────────────
        # ?year=2026              → all 12 months in 2026
        # ?year=2026&month=May    → every day of May 2026 (all days returned)
        # (no params)             → current year all months (default)
        import calendar as _cal
        from datetime import datetime as _dt

        MONTH_NAMES = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12,
        }
        MONTH_ABBR = {
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
            'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
        }

        year_param  = request.query_params.get('year',  '').strip()
        month_param = request.query_params.get('month', '').strip()

        _now = timezone.now()
        _year = int(year_param) if year_param and year_param.isdigit() else _now.year

        # Resolve month — accept name ("May"), abbreviation ("May"), or number ("05")
        # Default: current month (daily view)
        _month = None
        if month_param:
            mp_lower = month_param.lower()
            if month_param.isdigit() and 1 <= int(month_param) <= 12:
                _month = int(month_param)
            elif mp_lower in MONTH_NAMES:
                _month = MONTH_NAMES[mp_lower]
            elif mp_lower in MONTH_ABBR:
                _month = MONTH_ABBR[mp_lower]
        elif not year_param:
            # No params at all → default to current month daily view
            _month = _now.month
            _year  = _now.year

        if _month:
            _last_day = _cal.monthrange(_year, _month)[1]
            period_start = timezone.make_aware(_dt(_year, _month, 1, 0, 0, 0))
            period_end   = timezone.make_aware(_dt(_year, _month, _last_day, 23, 59, 59))
            use_daily = True
        else:
            # year only → all 12 months
            period_start = timezone.make_aware(_dt(_year, 1, 1, 0, 0, 0))
            period_end   = timezone.make_aware(_dt(_year, 12, 31, 23, 59, 59))
            use_daily = False

        # ── Build filtered querysets ───────────────────────────────────
        def _filter(qs, date_field='created_at'):
            return qs.filter(**{
                f'{date_field}__gte': period_start,
                f'{date_field}__lte': period_end,
            })
        # ── KPIs ──────────────────────────────────────────────────────
        # Total Revenue = total amount staked in the period
        # spins_qs = _filter(Spin.objects.all())
        # total_staked = spins_qs.aggregate(t=Sum('stake_amount'))['t'] or Decimal('0')
        # total_won = spins_qs.filter(outcome='win').aggregate(
        #     t=Sum('payout_amount')
        # )['t'] or Decimal('0')

        # # GGR = total staked - total won
        # ggr = total_staked - total_won
        # ── KPIs (per-currency, never summed) ──────────────────────────
        spins_qs = _filter(Spin.objects.all())

        # ── Split spins by source currency ────
        # naira_coins + bonus_coins (with bonus_destination='naira') → NGN side
        # crypto_coins + bonus_coins (with bonus_destination='crypto') → USDT side
        ngn_spins = spins_qs.filter(
            Q(source_wallet='naira_coins') |
            Q(source_wallet='bonus_coins', bonus_destination='naira')
        )
        usdt_spins = spins_qs.filter(
            Q(source_wallet='crypto_coins') |
            Q(source_wallet='bonus_coins', bonus_destination='crypto')
        )

        # ── NGN side ────
        ngn_staked = ngn_spins.aggregate(t=Sum('stake_amount'))['t'] or Decimal('0')
        ngn_won = ngn_spins.filter(outcome='win').aggregate(
            t=Sum('payout_amount')
        )['t'] or Decimal('0')
        ngn_ggr = ngn_staked - ngn_won

        # ── USDT side ────
        usdt_staked = usdt_spins.aggregate(t=Sum('stake_amount'))['t'] or Decimal('0')
        usdt_won = usdt_spins.filter(outcome='win').aggregate(
            t=Sum('payout_amount')
        )['t'] or Decimal('0')
        usdt_ggr = usdt_staked - usdt_won

        # ── Operational metrics (currency-agnostic) ────
        total_spins = spins_qs.count()
        winning_spins = spins_qs.filter(outcome='win').count()
        player_win_rate = (
            round((winning_spins / total_spins) * 100, 1)
            if total_spins > 0 else 0
        )

        # Realized house edge = (staked - won) / staked * 100
        # realized_house_edge = (
        #     round(float(ggr / total_staked) * 100, 2)
        #     if total_staked > 0 else Decimal('0')
        # )
        # Realized house edge — per currency
        ngn_house_edge = (
            round(float(ngn_ggr / ngn_staked) * 100, 2)
            if ngn_staked > 0 else 0
        )
        usdt_house_edge = (
            round(float(usdt_ggr / usdt_staked) * 100, 2)
            if usdt_staked > 0 else 0
        )

        # Player win rate = winning spins / total spins * 100
        total_spins = spins_qs.count()
        winning_spins = spins_qs.filter(outcome='win').count()
        player_win_rate = (
            round((winning_spins / total_spins) * 100, 1)
            if total_spins > 0 else 0
        )

        # Active users in period
        active_users_qs = User.objects.all()
        if period_start:
            active_users_qs = active_users_qs.filter(last_login__gte=period_start)
        active_users = active_users_qs.count()

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        new_today = User.objects.filter(created_at__gte=today_start).count()

        # ── Percentage change vs previous period ──────────────────────
        # Previous period = previous year (or previous month if daily view)
        if use_daily:
            # Compare to same month last year
            prev_year = _year - 1
            prev_month = _month if _month else 1
            prev_last = _cal.monthrange(prev_year, prev_month)[1]
            prev_start = timezone.make_aware(_dt(prev_year, prev_month, 1, 0, 0, 0))
            prev_end   = timezone.make_aware(_dt(prev_year, prev_month, prev_last, 23, 59, 59))
        else:
            # Compare to same year minus 1
            prev_start = timezone.make_aware(_dt(_year - 1, 1, 1, 0, 0, 0))
            prev_end   = timezone.make_aware(_dt(_year - 1, 12, 31, 23, 59, 59))

        # prev_spins = Spin.objects.filter(
        #     created_at__gte=prev_start, created_at__lte=prev_end,
        # )
        # prev_staked = prev_spins.aggregate(t=Sum('stake_amount'))['t'] or Decimal('0')
        # prev_won_amt = prev_spins.filter(outcome='win').aggregate(
        #     t=Sum('payout_amount')
        # )['t'] or Decimal('0')
        # prev_ggr = prev_staked - prev_won_amt
        # revenue_change = pct_change(total_staked, prev_staked)
        # ggr_change = pct_change(ggr, prev_ggr)
        prev_spins = Spin.objects.filter(
            created_at__gte=prev_start, created_at__lte=prev_end,
        )

        # Prev period NGN
        prev_ngn_spins = prev_spins.filter(
            Q(source_wallet='naira_coins') |
            Q(source_wallet='bonus_coins', bonus_destination='naira')
        )
        prev_ngn_staked = prev_ngn_spins.aggregate(
            t=Sum('stake_amount')
        )['t'] or Decimal('0')
        prev_ngn_won = prev_ngn_spins.filter(outcome='win').aggregate(
            t=Sum('payout_amount')
        )['t'] or Decimal('0')
        prev_ngn_ggr = prev_ngn_staked - prev_ngn_won

        # Prev period USDT
        prev_usdt_spins = prev_spins.filter(
            Q(source_wallet='crypto_coins') |
            Q(source_wallet='bonus_coins', bonus_destination='crypto')
        )
        prev_usdt_staked = prev_usdt_spins.aggregate(
            t=Sum('stake_amount')
        )['t'] or Decimal('0')
        prev_usdt_won = prev_usdt_spins.filter(outcome='win').aggregate(
            t=Sum('payout_amount')
        )['t'] or Decimal('0')
        prev_usdt_ggr = prev_usdt_staked - prev_usdt_won

        ngn_revenue_change = pct_change(ngn_staked, prev_ngn_staked)
        usdt_revenue_change = pct_change(usdt_staked, prev_usdt_staked)
        ngn_ggr_change = pct_change(ngn_ggr, prev_ngn_ggr)
        usdt_ggr_change = pct_change(usdt_ggr, prev_usdt_ggr)

        # ── Graph data ─────────────────────────────────────────────────
        if use_daily:
            # Daily breakdown — every day of the month included (zeros for no activity)
            # NGN daily series
            ngn_daily = (
                ngn_spins
                .annotate(day=TruncDay('created_at'))
                .values('day')
                .annotate(
                    staked=Sum('stake_amount'),
                    won=Sum('payout_amount'),
                    spin_count=Count('id'),
                )
            )
            # USDT daily series
            usdt_daily = (
                usdt_spins
                .annotate(day=TruncDay('created_at'))
                .values('day')
                .annotate(
                    staked=Sum('stake_amount'),
                    won=Sum('payout_amount'),
                    spin_count=Count('id'),
                )
            )

            ngn_lookup = {}
            for row in ngn_daily:
                if row['day']:
                    ngn_lookup[row['day'].day] = {
                        'staked': row['staked'] or Decimal('0'),
                        'won': row['won'] or Decimal('0'),
                        'spins': row['spin_count'],
                    }
            usdt_lookup = {}
            for row in usdt_daily:
                if row['day']:
                    usdt_lookup[row['day'].day] = {
                        'staked': row['staked'] or Decimal('0'),
                        'won': row['won'] or Decimal('0'),
                        'spins': row['spin_count'],
                    }

            month_name = MONTH_NAMES_LIST[_month] if _month else ''
            last_day_of_month = _cal.monthrange(_year, _month)[1]
            graph_data = []
            for day_num in range(1, last_day_of_month + 1):
                ngn_row = ngn_lookup.get(day_num, {
                    'staked': Decimal('0'), 'won': Decimal('0'), 'spins': 0,
                })
                usdt_row = usdt_lookup.get(day_num, {
                    'staked': Decimal('0'), 'won': Decimal('0'), 'spins': 0,
                })
                graph_data.append({
                    'day': day_num,
                    'month': month_name,
                    'year': _year,
                    'ngn': {
                        'staked': str(ngn_row['staked']),
                        'won': str(ngn_row['won']),
                        'ggr': str(ngn_row['staked'] - ngn_row['won']),
                        'spins': ngn_row['spins'],
                    },
                    'usdt': {
                        'staked': str(usdt_row['staked']),
                        'won': str(usdt_row['won']),
                        'ggr': str(usdt_row['staked'] - usdt_row['won']),
                        'spins': usdt_row['spins'],
                    },
                })
            # daily_qs = (
            #     spins_qs
            #     .annotate(day=TruncDay('created_at'))
            #     .values('day')
            #     .annotate(
            #         staked=Sum('stake_amount'),
            #         won=Sum('payout_amount'),
            #         spin_count=Count('id'),
            #     )
            # )
            # # Build lookup: day_of_month → data
            # daily_lookup = {}
            # for row in daily_qs:
            #     if row['day']:
            #         d_staked = row['staked'] or Decimal('0')
            #         d_won = row['won'] or Decimal('0')
            #         daily_lookup[row['day'].day] = {
            #             'staked': d_staked,
            #             'won': d_won,
            #             'spins': row['spin_count'],
            #         }

            # # Generate entry for EVERY day in the month
            # month_name = MONTH_NAMES_LIST[_month] if _month else ''
            # last_day_of_month = _cal.monthrange(_year, _month)[1]
            # graph_data = []
            # for day_num in range(1, last_day_of_month + 1):
            #     row = daily_lookup.get(day_num, {'staked': Decimal('0'), 'won': Decimal('0'), 'spins': 0})
            #     graph_data.append({
            #         'day': day_num,
            #         'month': month_name,
            #         'year': _year,
            #         'staked': str(row['staked']),
            #         'won': str(row['won']),
            #         'ggr': str(row['staked'] - row['won']),
            #         'spins': row['spins'],
            #     })
        else:
            # Monthly aggregation — every month of the year included (zeros for no activity)
            # Replace the existing monthly_qs block with:
            ngn_monthly = (
                ngn_spins
                .annotate(month=TruncMonth('created_at'))
                .values('month')
                .annotate(staked=Sum('stake_amount'), won=Sum('payout_amount'),
                          spin_count=Count('id'))
            )
            usdt_monthly = (
                usdt_spins
                .annotate(month=TruncMonth('created_at'))
                .values('month')
                .annotate(staked=Sum('stake_amount'), won=Sum('payout_amount'),
                          spin_count=Count('id'))
            )

            ngn_m_lookup = {}
            for row in ngn_monthly:
                if row['month']:
                    ngn_m_lookup[row['month'].month] = {
                        'staked': row['staked'] or Decimal('0'),
                        'won': row['won'] or Decimal('0'),
                        'spins': row['spin_count'],
                    }
            usdt_m_lookup = {}
            for row in usdt_monthly:
                if row['month']:
                    usdt_m_lookup[row['month'].month] = {
                        'staked': row['staked'] or Decimal('0'),
                        'won': row['won'] or Decimal('0'),
                        'spins': row['spin_count'],
                    }

            graph_data = []
            for m_num in range(1, 13):
                ngn_row = ngn_m_lookup.get(m_num, {
                    'staked': Decimal('0'), 'won': Decimal('0'), 'spins': 0,
                })
                usdt_row = usdt_m_lookup.get(m_num, {
                    'staked': Decimal('0'), 'won': Decimal('0'), 'spins': 0,
                })
                graph_data.append({
                    'month': MONTH_NAMES_LIST[m_num][:3],
                    'month_full': MONTH_NAMES_LIST[m_num],
                    'month_num': m_num,
                    'year': _year,
                    'ngn': {
                        'staked': str(ngn_row['staked']),
                        'won': str(ngn_row['won']),
                        'ggr': str(ngn_row['staked'] - ngn_row['won']),
                        'spins': ngn_row['spins'],
                    },
                    'usdt': {
                        'staked': str(usdt_row['staked']),
                        'won': str(usdt_row['won']),
                        'ggr': str(usdt_row['staked'] - usdt_row['won']),
                        'spins': usdt_row['spins'],
                    },
                })
            # monthly_qs = (
            #     spins_qs
            #     .annotate(month=TruncMonth('created_at'))
            #     .values('month')
            #     .annotate(
            #         staked=Sum('stake_amount'),
            #         won=Sum('payout_amount'),
            #         spin_count=Count('id'),
            #     )
            # )
            # # Build lookup: month_number → data
            # monthly_lookup = {}
            # for row in monthly_qs:
            #     if row['month']:
            #         m_staked = row['staked'] or Decimal('0')
            #         m_won = row['won'] or Decimal('0')
            #         monthly_lookup[row['month'].month] = {
            #             'staked': m_staked,
            #             'won': m_won,
            #             'spins': row['spin_count'],
            #         }

            # # Generate entry for EVERY month (1-12)
            # graph_data = []
            # for m_num in range(1, 13):
            #     row = monthly_lookup.get(m_num, {'staked': Decimal('0'), 'won': Decimal('0'), 'spins': 0})
            #     graph_data.append({
            #         'month': MONTH_NAMES_LIST[m_num][:3],  # "Jan", "Feb" etc.
            #         'month_full': MONTH_NAMES_LIST[m_num],
            #         'month_num': m_num,
            #         'year': _year,
            #         'staked': str(row['staked']),
            #         'won': str(row['won']),
            #         'ggr': str(row['staked'] - row['won']),
            #         'spins': row['spins'],
            #     })

        # ── Recent Spins (last 10) ────────────────────────────────────
        recent_spins = []
        for spin in Spin.objects.select_related(
            'user', 'segment_landed', 'wheel'
        ).order_by('-created_at')[:10]:
            recent_spins.append({
                'id': str(spin.id),
                'user': format_user(spin.user),
                'stake': str(spin.stake_amount),
                'result': (
                    spin.segment_landed.label
                    if spin.segment_landed else spin.outcome
                ),
                'multiplier': (
                    str(spin.segment_landed.multiplier)
                    if spin.segment_landed else '0'
                ),
                'win_value': str(spin.payout_amount),
                'outcome': spin.outcome,
                'date': spin.created_at.strftime('%b %-d, %Y'),
            })

        # ── Top Winners ────────────────────────────────────────────────
        # Top winners — separate leaderboards per currency
        ngn_top = (
            ngn_spins.filter(outcome='win')
            .values('user_id')
            .annotate(total_won=Sum('payout_amount'))
            .order_by('-total_won')[:10]
        )
        usdt_top = (
            usdt_spins.filter(outcome='win')
            .values('user_id')
            .annotate(total_won=Sum('payout_amount'))
            .order_by('-total_won')[:10]
        )

        top_winners_ngn = []
        for row in ngn_top:
            try:
                u = User.objects.get(id=row['user_id'])
                top_winners_ngn.append({
                    'user': format_user(u),
                    'win_value': str(row['total_won']),
                })
            except User.DoesNotExist:
                pass

        top_winners_usdt = []
        for row in usdt_top:
            try:
                u = User.objects.get(id=row['user_id'])
                top_winners_usdt.append({
                    'user': format_user(u),
                    'win_value': str(row['total_won']),
                })
            except User.DoesNotExist:
                pass
        # top_winners = []
        # top_qs = (
        #     spins_qs.filter(outcome='win')
        #     .values('user_id')
        #     .annotate(total_won=Sum('payout_amount'))
        #     .order_by('-total_won')[:10]
        # )
        # for row in top_qs:
        #     try:
        #         u = User.objects.get(id=row['user_id'])
        #         top_winners.append({
        #             'user': format_user(u),
        #             'win_value': str(row['total_won']),
        #         })
        #     except User.DoesNotExist:
        #         pass
        return Response({
            'success': True,
            'data': {
                'period': 'month' if use_daily else 'year',
                'year': _year,
                'month': MONTH_NAMES_LIST[_month] if _month and 1 <= _month <= 12 else None,
                'kpis': {
                    # Per-currency financial KPIs (NEVER summed)
                    'ngn': {
                        'total_revenue': str(ngn_staked),
                        'total_revenue_change_pct': ngn_revenue_change,
                        'net_profit_ggr': str(ngn_ggr),
                        'net_profit_ggr_change_pct': ngn_ggr_change,
                        'realized_house_edge_pct': str(ngn_house_edge),
                        'total_won_by_players': str(ngn_won),
                    },
                    'usdt': {
                        'total_revenue': str(usdt_staked),
                        'total_revenue_change_pct': usdt_revenue_change,
                        'net_profit_ggr': str(usdt_ggr),
                        'net_profit_ggr_change_pct': usdt_ggr_change,
                        'realized_house_edge_pct': str(usdt_house_edge),
                        'total_won_by_players': str(usdt_won),
                    },
                    # Currency-agnostic operational metrics
                    'player_win_rate_pct': str(player_win_rate),
                    'total_spins': total_spins,
                    'winning_spins': winning_spins,
                    'active_users': active_users,
                    'new_users_today': new_today,
                },
                'graph': graph_data,
                'recent_spins': recent_spins,
                'top_winners': {
                    'ngn': top_winners_ngn,
                    'usdt': top_winners_usdt,
                },
            },
        })

        # return Response({
        #     'success': True,
        #     'data': {
        #         'period': 'month' if use_daily else 'year',
        #         'year': _year,
        #         'month': MONTH_NAMES_LIST[_month] if _month and 1 <= _month <= 12 else None,
        #         'kpis': {
        #             'total_revenue': str(total_staked),
        #             'total_revenue_change_pct': revenue_change,
        #             'net_profit_ggr': str(ggr),
        #             'net_profit_ggr_change_pct': ggr_change,
        #             'realized_house_edge_pct': str(realized_house_edge),
        #             'player_win_rate_pct': str(player_win_rate),
        #             'total_spins': total_spins,
        #             'winning_spins': winning_spins,
        #             'active_users': active_users,
        #             'new_users_today': new_today,
        #         },
        #         'graph': graph_data,
        #         'recent_spins': recent_spins,
        #         'top_winners': top_winners,
        #     },
        # })


# ══════════════════════════════════════════════════════════════════════════════
# SCREEN 2 — FINANCIALS
# ══════════════════════════════════════════════════════════════════════════════

class FinancialsView(APIView):
    """
    GET /api/v1/admin/financials/

    Query params:
      year      4-digit year e.g. 2026 (default: current year)
      month     2-digit month e.g. 05 (only valid with year)

    Behavior:
      ?year=2026          → monthly breakdown for all of 2026
      ?year=2026&month=05 → daily breakdown for May 2026
      (no params)         → monthly breakdown for current year

    Returns all 7 financial sections.
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        from apps.spin.models import Spin
        from apps.wallet.models import Transaction
        from apps.withdrawals.models import Withdrawal
        from django.db.models.functions import TruncDay, TruncMonth

        MONTH_NAMES_LIST = [
            '', 'January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December',
        ]

        now = timezone.now()


        # ── Parse year + month params ─────────────────────────────────
        # ?year=2026              → all 12 months in 2026
        # ?year=2026&month=May    → every day of May 2026 (all days returned)
        # (no params)             → current year all months (default)
        import calendar as _cal
        from datetime import datetime as _dt

        MONTH_NAMES = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12,
        }
        MONTH_ABBR = {
            'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
            'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
        }

        year_param  = request.query_params.get('year',  '').strip()
        month_param = request.query_params.get('month', '').strip()

        _now = timezone.now()
        _year = int(year_param) if year_param and year_param.isdigit() else _now.year

        # Resolve month — accept name ("May"), abbreviation ("May"), or number ("05")
        # Default: current month (daily view)
        _month = None
        if month_param:
            mp_lower = month_param.lower()
            if month_param.isdigit() and 1 <= int(month_param) <= 12:
                _month = int(month_param)
            elif mp_lower in MONTH_NAMES:
                _month = MONTH_NAMES[mp_lower]
            elif mp_lower in MONTH_ABBR:
                _month = MONTH_ABBR[mp_lower]
        elif not year_param:
            # No params at all → default to current month daily view
            _month = _now.month
            _year  = _now.year

        if _month:
            _last_day = _cal.monthrange(_year, _month)[1]
            period_start = timezone.make_aware(_dt(_year, _month, 1, 0, 0, 0))
            period_end   = timezone.make_aware(_dt(_year, _month, _last_day, 23, 59, 59))
            use_daily = True
        else:
            # year only → all 12 months
            period_start = timezone.make_aware(_dt(_year, 1, 1, 0, 0, 0))
            period_end   = timezone.make_aware(_dt(_year, 12, 31, 23, 59, 59))
            use_daily = False

        # ── Build filtered querysets ───────────────────────────────────
        def _filter(qs, date_field='created_at'):
            return qs.filter(**{
                f'{date_field}__gte': period_start,
                f'{date_field}__lte': period_end,
            })
        # Previous period for % change
        # Previous period = previous year or previous month
        if use_daily:
            prev_yr = _year - 1
            prev_mo = _month if _month else 1
            prev_last = _cal.monthrange(prev_yr, prev_mo)[1]
            prev_start = timezone.make_aware(_dt(prev_yr, prev_mo, 1, 0, 0, 0))
            prev_end   = timezone.make_aware(_dt(prev_yr, prev_mo, prev_last, 23, 59, 59))
        else:
            prev_start = timezone.make_aware(_dt(_year - 1, 1, 1, 0, 0, 0))
            prev_end   = timezone.make_aware(_dt(_year - 1, 12, 31, 23, 59, 59))

        def _prev_qs(qs, date_field='created_at'):
            return qs.filter(**{
                f'{date_field}__gte': prev_start,
                f'{date_field}__lte': prev_end,
            })

        # ── 1. DEPOSITS ────────────────────────────────────────────────
        # ── 1. DEPOSITS (per-currency — Transaction.currency tracks NGN/USDT) ──
        # Filter Transaction by currency (added in Chunk 1)
        dep_base = Transaction.objects.filter(type='deposit', status='completed')

        # NGN deposits
        ngn_dep_qs = _filter(dep_base.filter(currency='NGN'))
        ngn_dep_amount = ngn_dep_qs.aggregate(t=Sum('amount'))['t'] or Decimal('0')
        ngn_dep_count = ngn_dep_qs.count()
        ngn_avg_dep = (
            ngn_dep_amount / ngn_dep_count if ngn_dep_count else Decimal('0')
        )
        prev_ngn_dep = _prev_qs(dep_base.filter(currency='NGN')).aggregate(
            t=Sum('amount')
        )['t'] or Decimal('0')

        # USDT deposits
        usdt_dep_qs = _filter(dep_base.filter(currency='USDT'))
        usdt_dep_amount = usdt_dep_qs.aggregate(t=Sum('amount'))['t'] or Decimal('0')
        usdt_dep_count = usdt_dep_qs.count()
        usdt_avg_dep = (
            usdt_dep_amount / usdt_dep_count if usdt_dep_count else Decimal('0')
        )
        prev_usdt_dep = _prev_qs(dep_base.filter(currency='USDT')).aggregate(
            t=Sum('amount')
        )['t'] or Decimal('0')

        # Net position — per currency (deposits - withdrawals, all time)
        all_ngn_deposits = dep_base.filter(currency='NGN').aggregate(
            t=Sum('amount')
        )['t'] or Decimal('0')
        all_usdt_deposits = dep_base.filter(currency='USDT').aggregate(
            t=Sum('amount')
        )['t'] or Decimal('0')
        all_ngn_withdrawals = Withdrawal.objects.filter(
            currency='NGN', status='completed',
        ).aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
        all_usdt_withdrawals = Withdrawal.objects.filter(
            currency='USDT', status='completed',
        ).aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
        net_position_ngn = all_ngn_deposits - all_ngn_withdrawals
        net_position_usdt = all_usdt_deposits - all_usdt_withdrawals
        # dep_qs = _filter(
        #     Transaction.objects.filter(type='deposit', status='completed')
        # )
        # total_deposit_amount = dep_qs.aggregate(t=Sum('amount'))['t'] or Decimal('0')
        # total_deposit_count = dep_qs.count()
        # avg_deposit = (
        #     total_deposit_amount / total_deposit_count
        #     if total_deposit_count else Decimal('0')
        # )

        # prev_dep_amount = _prev_qs(
        #     Transaction.objects.filter(type='deposit', status='completed')
        # ).aggregate(t=Sum('amount'))['t'] or Decimal('0')

        # # Net position = all deposits - all withdrawals (all time)
        # total_deposited_ever = Transaction.objects.filter(
        #     type='deposit', status='completed'
        # ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
        # total_withdrawn_ever = Withdrawal.objects.filter(
        #     status='completed'
        # ).aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
        # net_position = total_deposited_ever - total_withdrawn_ever

        # ── 2. WITHDRAWALS ─────────────────────────────────────────────
        # ── 2. WITHDRAWALS (per-currency) ──────────────────────────────
        wd_base = Withdrawal.objects.filter(status='completed')

        # NGN withdrawals
        ngn_wd_qs = _filter(
            wd_base.filter(currency='NGN'), date_field='completed_at',
        )
        ngn_wd_amount = ngn_wd_qs.aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
        ngn_wd_count = ngn_wd_qs.count()
        prev_ngn_wd = _prev_qs(
            wd_base.filter(currency='NGN'), date_field='completed_at',
        ).aggregate(t=Sum('net_amount'))['t'] or Decimal('0')

        # USDT withdrawals
        usdt_wd_qs = _filter(
            wd_base.filter(currency='USDT'), date_field='completed_at',
        )
        usdt_wd_amount = usdt_wd_qs.aggregate(
            t=Sum('net_amount')
        )['t'] or Decimal('0')
        usdt_wd_count = usdt_wd_qs.count()
        prev_usdt_wd = _prev_qs(
            wd_base.filter(currency='USDT'), date_field='completed_at',
        ).aggregate(t=Sum('net_amount'))['t'] or Decimal('0')

        # % of deposits — per currency
        ngn_pct_of_dep = (
            round(float(ngn_wd_amount / ngn_dep_amount) * 100, 1)
            if ngn_dep_amount > 0 else 0
        )
        usdt_pct_of_dep = (
            round(float(usdt_wd_amount / usdt_dep_amount) * 100, 1)
            if usdt_dep_amount > 0 else 0
        )

        # Pending — per currency
        pending_base = Withdrawal.objects.filter(
            status__in=['pending_review', 'pending', 'processing'],
        )
        ngn_pending_amount = pending_base.filter(currency='NGN').aggregate(
            t=Sum('amount')
        )['t'] or Decimal('0')
        usdt_pending_amount = pending_base.filter(currency='USDT').aggregate(
            t=Sum('amount')
        )['t'] or Decimal('0')

        ngn_pending_count = Withdrawal.objects.filter(
            currency='NGN', status='pending_review',
        ).count()
        usdt_pending_count = Withdrawal.objects.filter(
            currency='USDT', status='pending_review',
        ).count()

        # Success rate per currency
        ngn_all = _filter(
            Withdrawal.objects.filter(currency='NGN'),
            date_field='requested_at',
        )
        ngn_success_rate = (
            round((ngn_wd_count / ngn_all.count()) * 100, 1)
            if ngn_all.count() else 0
        )
        usdt_all = _filter(
            Withdrawal.objects.filter(currency='USDT'),
            date_field='requested_at',
        )
        usdt_success_rate = (
            round((usdt_wd_count / usdt_all.count()) * 100, 1)
            if usdt_all.count() else 0
        )
        # wd_qs = _filter(
        #     Withdrawal.objects.filter(status='completed'), date_field='completed_at'
        # )
        # total_wd_amount = wd_qs.aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
        # total_wd_count = wd_qs.count()

        # prev_wd_amount = _prev_qs(
        #     Withdrawal.objects.filter(status='completed'), date_field='completed_at'
        # ).aggregate(t=Sum('net_amount'))['t'] or Decimal('0')

        # pct_of_deposits = (
        #     round(float(total_wd_amount / total_deposit_amount) * 100, 1)
        #     if total_deposit_amount > 0 else 0
        # )

        # pending_wd_amount = Withdrawal.objects.filter(
        #     status__in=['pending_review', 'pending', 'processing'],
        # ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
        # pending_wd_count = Withdrawal.objects.filter(
        #     status='pending_review',
        # ).count()

        # # Rate of successful withdrawals
        # all_wd = _filter(Withdrawal.objects.all(), date_field='requested_at')
        # all_wd_count = all_wd.count()
        # success_rate = (
        #     round((total_wd_count / all_wd_count) * 100, 1)
        #     if all_wd_count else 0
        # )

        # ── 3. SPINS ──────────────────────────────────────────────────
        # ── 3. SPINS ──────────────────────────────────────────────────
        spins_qs = _filter(Spin.objects.all())
        total_spins = spins_qs.count()
        wins_count = spins_qs.filter(outcome='win').count()
        losses_count = spins_qs.filter(outcome='loss').count()
        win_rate = round((wins_count / total_spins) * 100, 1) if total_spins else 0

        # Split spins by source currency
        ngn_spins = spins_qs.filter(
            Q(source_wallet='naira_coins') |
            Q(source_wallet='bonus_coins', bonus_destination='naira')
        )
        usdt_spins = spins_qs.filter(
            Q(source_wallet='crypto_coins') |
            Q(source_wallet='bonus_coins', bonus_destination='crypto')
        )

        ngn_staked = ngn_spins.aggregate(t=Sum('stake_amount'))['t'] or Decimal('0')
        usdt_staked = usdt_spins.aggregate(t=Sum('stake_amount'))['t'] or Decimal('0')

        ngn_spin_count = ngn_spins.count()
        usdt_spin_count = usdt_spins.count()

        ngn_avg_stake = (
            ngn_staked / ngn_spin_count if ngn_spin_count else Decimal('0')
        )
        usdt_avg_stake = (
            usdt_staked / usdt_spin_count if usdt_spin_count else Decimal('0')
        )
        # spins_qs = _filter(Spin.objects.all())
        # total_spins = spins_qs.count()
        # wins_count = spins_qs.filter(outcome='win').count()
        # losses_count = spins_qs.filter(outcome='loss').count()
        # win_rate = round((wins_count / total_spins) * 100, 1) if total_spins else 0

        # spins_staked = spins_qs.aggregate(t=Sum('stake_amount'))['t'] or Decimal('0')
        # avg_stake_per_spin = (
        #     spins_staked / total_spins if total_spins else Decimal('0')
        # )

        # ── 4. GGR ────────────────────────────────────────────────────
        # ── 4. GGR (per currency) ─────────────────────────────────────
        ngn_won = ngn_spins.filter(outcome='win').aggregate(
            t=Sum('payout_amount')
        )['t'] or Decimal('0')
        usdt_won = usdt_spins.filter(outcome='win').aggregate(
            t=Sum('payout_amount')
        )['t'] or Decimal('0')

        ngn_ggr_amount = ngn_staked - ngn_won
        usdt_ggr_amount = usdt_staked - usdt_won

        ngn_ggr_margin = (
            round(float(ngn_ggr_amount / ngn_staked) * 100, 2)
            if ngn_staked > 0 else 0
        )
        usdt_ggr_margin = (
            round(float(usdt_ggr_amount / usdt_staked) * 100, 2)
            if usdt_staked > 0 else 0
        )

        # Previous period for GGR change
        prev_spins_qs = _prev_qs(Spin.objects.all())
        prev_ngn_spins = prev_spins_qs.filter(
            Q(source_wallet='naira_coins') |
            Q(source_wallet='bonus_coins', bonus_destination='naira')
        )
        prev_usdt_spins = prev_spins_qs.filter(
            Q(source_wallet='crypto_coins') |
            Q(source_wallet='bonus_coins', bonus_destination='crypto')
        )

        prev_ngn_staked = prev_ngn_spins.aggregate(
            t=Sum('stake_amount')
        )['t'] or Decimal('0')
        prev_ngn_won = prev_ngn_spins.filter(outcome='win').aggregate(
            t=Sum('payout_amount')
        )['t'] or Decimal('0')
        prev_ngn_ggr = prev_ngn_staked - prev_ngn_won

        prev_usdt_staked = prev_usdt_spins.aggregate(
            t=Sum('stake_amount')
        )['t'] or Decimal('0')
        prev_usdt_won = prev_usdt_spins.filter(outcome='win').aggregate(
            t=Sum('payout_amount')
        )['t'] or Decimal('0')
        prev_usdt_ggr = prev_usdt_staked - prev_usdt_won
        # total_won_amount = spins_qs.filter(
        #     outcome='win'
        # ).aggregate(t=Sum('payout_amount'))['t'] or Decimal('0')
        # ggr_amount = spins_staked - total_won_amount
        # ggr_margin = (
        #     round(float(ggr_amount / spins_staked) * 100, 2)
        #     if spins_staked > 0 else 0
        # )

        # prev_spins_qs = _prev_qs(Spin.objects.all())
        # prev_staked = prev_spins_qs.aggregate(
        #     t=Sum('stake_amount')
        # )['t'] or Decimal('0')
        # prev_won = prev_spins_qs.filter(
        #     outcome='win'
        # ).aggregate(t=Sum('payout_amount'))['t'] or Decimal('0')
        # prev_ggr = prev_staked - prev_won

        # ── 5. CASH FLOW (filtered chart) ─────────────────────────────
        if use_daily:
            trunc_fn = TruncDay
            date_fmt = '%-d %b'
            label_key = 'date'
        else:
            trunc_fn = TruncMonth
            date_fmt = '%b'
            label_key = 'month'

        # dep_chart_qs = (
        #     _filter(
        #         Transaction.objects.filter(type='deposit', status='completed')
        #     )
        #     .annotate(period=trunc_fn('created_at'))
        #     .values('period')
        #     .annotate(count=Count('id'), total=Sum('amount'))
        # )
        # dep_lookup = {
        #     row['period'].day if use_daily else row['period'].month: row
        #     for row in dep_chart_qs if row['period']
        # }
        # NGN deposits chart
        ngn_dep_chart = (
            _filter(dep_base.filter(currency='NGN'))
            .annotate(period=trunc_fn('created_at'))
            .values('period')
            .annotate(count=Count('id'), total=Sum('amount'))
        )
        ngn_dep_chart_lookup = {
            row['period'].day if use_daily else row['period'].month: row
            for row in ngn_dep_chart if row['period']
        }

        # USDT deposits chart
        usdt_dep_chart = (
            _filter(dep_base.filter(currency='USDT'))
            .annotate(period=trunc_fn('created_at'))
            .values('period')
            .annotate(count=Count('id'), total=Sum('amount'))
        )
        usdt_dep_chart_lookup = {
            row['period'].day if use_daily else row['period'].month: row
            for row in usdt_dep_chart if row['period']
        }

        # wd_chart_qs = (
        #     _filter(
        #         Withdrawal.objects.filter(status='completed'),
        #         date_field='completed_at',
        #     )
        #     .annotate(period=trunc_fn('completed_at'))
        #     .values('period')
        #     .annotate(count=Count('id'), total=Sum('net_amount'))
        # )
        # wd_lookup = {
        #     row['period'].day if use_daily else row['period'].month: row
        #     for row in wd_chart_qs if row['period']
        # }
        # NGN withdrawals chart
        ngn_wd_chart = (
            _filter(
                wd_base.filter(currency='NGN'),
                date_field='completed_at',
            )
            .annotate(period=trunc_fn('completed_at'))
            .values('period')
            .annotate(count=Count('id'), total=Sum('net_amount'))
        )
        ngn_wd_chart_lookup = {
            row['period'].day if use_daily else row['period'].month: row
            for row in ngn_wd_chart if row['period']
        }

        # USDT withdrawals chart
        usdt_wd_chart = (
            _filter(
                wd_base.filter(currency='USDT'),
                date_field='completed_at',
            )
            .annotate(period=trunc_fn('completed_at'))
            .values('period')
            .annotate(count=Count('id'), total=Sum('net_amount'))
        )
        usdt_wd_chart_lookup = {
            row['period'].day if use_daily else row['period'].month: row
            for row in usdt_wd_chart if row['period']
        }

        if use_daily:
            _fin_last = _cal.monthrange(_year, _month)[1]
            _fin_range = range(1, _fin_last + 1)
            _fin_mname = MONTH_NAMES_LIST[_month] if _month else ''
        else:
            _fin_range = range(1, 13)

        cash_flow_deposits = []
        cash_flow_withdrawals = []
        cash_flow_ngn_deposits = []
        cash_flow_ngn_withdrawals = []
        cash_flow_usdt_deposits = []
        cash_flow_usdt_withdrawals = []
        for key in _fin_range:
            if use_daily:
                label = {'day': key, 'month': _fin_mname, 'year': _year}
            else:
                label = {
                    'month': MONTH_NAMES_LIST[key][:3],
                    'month_num': key, 'year': _year,
                }

            n_dep = ngn_dep_chart_lookup.get(key, {})
            n_wd = ngn_wd_chart_lookup.get(key, {})
            u_dep = usdt_dep_chart_lookup.get(key, {})
            u_wd = usdt_wd_chart_lookup.get(key, {})

            cash_flow_ngn_deposits.append({
                **label,
                'count': n_dep.get('count', 0),
                'amount': str(n_dep.get('total') or 0),
            })
            cash_flow_ngn_withdrawals.append({
                **label,
                'count': n_wd.get('count', 0),
                'amount': str(n_wd.get('total') or 0),
            })
            cash_flow_usdt_deposits.append({
                **label,
                'count': u_dep.get('count', 0),
                'amount': str(u_dep.get('total') or 0),
            })
            cash_flow_usdt_withdrawals.append({
                **label,
                'count': u_wd.get('count', 0),
                'amount': str(u_wd.get('total') or 0),
            })
        # for key in _fin_range:
        #     dep_row = dep_lookup.get(key, {})
        #     wd_row  = wd_lookup.get(key, {})
        #     if use_daily:
        #         label = {'day': key, 'month': _fin_mname, 'year': _year}
        #     else:
        #         label = {'month': MONTH_NAMES_LIST[key][:3], 'month_num': key, 'year': _year}

        #     cash_flow_deposits.append({
        #         **label,
        #         'count': dep_row.get('count', 0),
        #         'amount': str(dep_row.get('total') or 0),
        #     })
        #     cash_flow_withdrawals.append({
        #         **label,
        #         'count': wd_row.get('count', 0),
        #         'amount': str(wd_row.get('total') or 0),
        #     })

        # ── 6. SPIN BREAKDOWN ─────────────────────────────────────────
        spin_breakdown = {
            'ngn': {
                'total_ggr': str(ngn_ggr_amount),
                'total_won_by_players': str(ngn_won),
                'total_staked': str(ngn_staked),
            },
            'usdt': {
                'total_ggr': str(usdt_ggr_amount),
                'total_won_by_players': str(usdt_won),
                'total_staked': str(usdt_staked),
            },
        }
        # spin_breakdown = {
        #     'total_ggr': str(ggr_amount),
        #     'total_won_by_players': str(total_won_amount),
        #     'total_staked': str(spins_staked),
        # }

        # ── 7. GGR TREND (filtered) ───────────────────────────────────
        # NGN GGR trend
        ngn_ggr_trend_qs = (
            ngn_spins
            .annotate(period=trunc_fn('created_at'))
            .values('period')
            .annotate(staked=Sum('stake_amount'), won=Sum('payout_amount'))
        )
        ngn_ggr_lookup = {
            row['period'].day if use_daily else row['period'].month: row
            for row in ngn_ggr_trend_qs if row['period']
        }

        # USDT GGR trend
        usdt_ggr_trend_qs = (
            usdt_spins
            .annotate(period=trunc_fn('created_at'))
            .values('period')
            .annotate(staked=Sum('stake_amount'), won=Sum('payout_amount'))
        )
        usdt_ggr_lookup = {
            row['period'].day if use_daily else row['period'].month: row
            for row in usdt_ggr_trend_qs if row['period']
        }

        ngn_ggr_trend = []
        usdt_ggr_trend = []
        for key in _fin_range:
            if use_daily:
                label = {'day': key, 'month': _fin_mname, 'year': _year}
            else:
                label = {
                    'month': MONTH_NAMES_LIST[key][:3],
                    'month_num': key, 'year': _year,
                }

            n_row = ngn_ggr_lookup.get(key, {})
            n_st = n_row.get('staked') or Decimal('0')
            n_won = n_row.get('won') or Decimal('0')

            u_row = usdt_ggr_lookup.get(key, {})
            u_st = u_row.get('staked') or Decimal('0')
            u_won = u_row.get('won') or Decimal('0')

            ngn_ggr_trend.append({
                **label,
                'ggr': str(n_st - n_won),
                'staked': str(n_st),
                'won': str(n_won),
            })
            usdt_ggr_trend.append({
                **label,
                'ggr': str(u_st - u_won),
                'staked': str(u_st),
                'won': str(u_won),
            })
        # ggr_trend_qs = (
        #     spins_qs
        #     .annotate(period=trunc_fn('created_at'))
        #     .values('period')
        #     .annotate(staked=Sum('stake_amount'), won=Sum('payout_amount'))
        # )
        # ggr_lookup = {
        #     row['period'].day if use_daily else row['period'].month: row
        #     for row in ggr_trend_qs if row['period']
        # }

        # ggr_trend = []
        # for key in _fin_range:
        #     row = ggr_lookup.get(key, {})
        #     g_staked = row.get('staked') or Decimal('0')
        #     g_won    = row.get('won') or Decimal('0')
        #     if use_daily:
        #         label = {'day': key, 'month': _fin_mname, 'year': _year}
        #     else:
        #         label = {'month': MONTH_NAMES_LIST[key][:3], 'month_num': key, 'year': _year}
        #     ggr_trend.append({
        #         **label,
        #         'ggr': str(g_staked - g_won),
        #         'staked': str(g_staked),
        #         'won': str(g_won),
        #     })

        return Response({
            'success': True,
            'data': {
                'period': 'month' if use_daily else 'year',
                'year': _year,
                'month': MONTH_NAMES_LIST[_month] if _month and 1 <= _month <= 12 else None,

                # Section 1 — Deposits (per currency)
                'deposits': {
                    'ngn': {
                        'total_amount': str(ngn_dep_amount),
                        'total_amount_change_pct': pct_change(ngn_dep_amount, prev_ngn_dep),
                        'total_transactions': ngn_dep_count,
                        'average_per_deposit': str(round(ngn_avg_dep, 2)),
                        'net_position': str(net_position_ngn),
                    },
                    'usdt': {
                        'total_amount': str(usdt_dep_amount),
                        'total_amount_change_pct': pct_change(usdt_dep_amount, prev_usdt_dep),
                        'total_transactions': usdt_dep_count,
                        'average_per_deposit': str(round(usdt_avg_dep, 2)),
                        'net_position': str(net_position_usdt),
                    },
                },

                # Section 2 — Withdrawals (per currency)
                'withdrawals': {
                    'ngn': {
                        'total_amount': str(ngn_wd_amount),
                        'total_amount_change_pct': pct_change(ngn_wd_amount, prev_ngn_wd),
                        'pct_of_deposits': str(ngn_pct_of_dep),
                        'pending_amount': str(ngn_pending_amount),
                        'pending_queue_count': ngn_pending_count,
                        'success_rate_pct': str(ngn_success_rate),
                    },
                    'usdt': {
                        'total_amount': str(usdt_wd_amount),
                        'total_amount_change_pct': pct_change(usdt_wd_amount, prev_usdt_wd),
                        'pct_of_deposits': str(usdt_pct_of_dep),
                        'pending_amount': str(usdt_pending_amount),
                        'pending_queue_count': usdt_pending_count,
                        'success_rate_pct': str(usdt_success_rate),
                    },
                },

                # Section 3 — Spins (per currency where applicable)
                'spins': {
                    'total_spins': total_spins,
                    'win_rate_pct': str(win_rate),
                    'wins_count': wins_count,
                    'losses_count': losses_count,
                    'ngn': {
                        'count': ngn_spin_count,
                        'total_staked': str(ngn_staked),
                        'avg_stake_per_spin': str(round(ngn_avg_stake, 2)),
                    },
                    'usdt': {
                        'count': usdt_spin_count,
                        'total_staked': str(usdt_staked),
                        'avg_stake_per_spin': str(round(usdt_avg_stake, 2)),
                    },
                },

                # Section 4 — GGR (per currency)
                'ggr': {
                    'ngn': {
                        'ggr_amount': str(ngn_ggr_amount),
                        'ggr_change_pct': pct_change(ngn_ggr_amount, prev_ngn_ggr),
                        'ggr_margin_pct': str(ngn_ggr_margin),
                        'total_staked': str(ngn_staked),
                        'total_won': str(ngn_won),
                    },
                    'usdt': {
                        'ggr_amount': str(usdt_ggr_amount),
                        'ggr_change_pct': pct_change(usdt_ggr_amount, prev_usdt_ggr),
                        'ggr_margin_pct': str(usdt_ggr_margin),
                        'total_staked': str(usdt_staked),
                        'total_won': str(usdt_won),
                    },
                },

                # Section 5 — Cash Flow (per currency)
                'cash_flow': {
                    'ngn': {
                        'deposits': cash_flow_ngn_deposits,
                        'withdrawals': cash_flow_ngn_withdrawals,
                    },
                    'usdt': {
                        'deposits': cash_flow_usdt_deposits,
                        'withdrawals': cash_flow_usdt_withdrawals,
                    },
                },

                # Section 6 — Spin Breakdown (per currency)
                'spin_breakdown': spin_breakdown,

                # Section 7 — GGR Trend (per currency)
                'ggr_trend': {
                    'ngn': ngn_ggr_trend,
                    'usdt': usdt_ggr_trend,
                },
            },
        })
        # return Response({
        #     'success': True,
        #     'data': {
        #         'period': 'month' if use_daily else 'year',
        #         'year': _year,
        #         'month': MONTH_NAMES_LIST[_month] if _month and 1 <= _month <= 12 else None,

        #         # Section 1 — Deposits
        #         'deposits': {
        #             'total_amount': str(total_deposit_amount),
        #             'total_amount_change_pct': pct_change(total_deposit_amount, prev_dep_amount),
        #             'total_transactions': total_deposit_count,
        #             'average_per_deposit': str(round(avg_deposit, 2)),
        #             'net_position': str(net_position),
        #         },

        #         # Section 2 — Withdrawals
        #         'withdrawals': {
        #             'total_amount': str(total_wd_amount),
        #             'total_amount_change_pct': pct_change(total_wd_amount, prev_wd_amount),
        #             'pct_of_deposits': str(pct_of_deposits),
        #             'pending_amount': str(pending_wd_amount),
        #             'pending_queue_count': pending_wd_count,
        #             'success_rate_pct': str(success_rate),
        #         },

        #         # Section 3 — Spins
        #         'spins': {
        #             'total_spins': total_spins,
        #             'win_rate_pct': str(win_rate),
        #             'wins_count': wins_count,
        #             'losses_count': losses_count,
        #             'average_stake_per_spin': str(round(avg_stake_per_spin, 2)),
        #         },

        #         # Section 4 — GGR
        #         'ggr': {
        #             'ggr_amount': str(ggr_amount),
        #             'ggr_change_pct': pct_change(ggr_amount, prev_ggr),
        #             'ggr_margin_pct': str(ggr_margin),
        #             'total_staked': str(spins_staked),
        #             'total_won': str(total_won_amount),
        #             'average_stake_per_spin': str(round(avg_stake_per_spin, 2)),
        #         },

        #         # Section 5 — Cash Flow chart
        #         'cash_flow': {
        #             'deposits': cash_flow_deposits,
        #             'withdrawals': cash_flow_withdrawals,
        #         },

        #         # Section 6 — Spin Breakdown
        #         'spin_breakdown': spin_breakdown,

        #         # Section 7 — GGR Trend chart
        #         'ggr_trend': ggr_trend,
        #     },
        # })


# ══════════════════════════════════════════════════════════════════════════════
# SCREEN 3 — RTP CONTROL
# ══════════════════════════════════════════════════════════════════════════════

class RTPControlListCreateView(APIView):
    """
    GET  /api/v1/admin/rtp/    List all wheel/stake configurations with segment probabilities
    POST /api/v1/admin/rtp/    Create a new wheel config

    POST body:
    {
      "name": "Entry Stake (₦200-₦10,000)",
      "wheel_type": "standard",
      "currency_type": "coin",
      "min_stake": "200",
      "max_stake": "10000",
      "rtp_target": "80",
      "is_active": false,
      "is_welcome_only": false,
      "segments": [
        {"label": "Loss", "multiplier": "0", "probability_weight": 30, "color": "#3a3a3a"},
        {"label": "0.5x", "multiplier": "0.5", "probability_weight": 20, "color": "#5a5a5a"},
        {"label": "1x",   "multiplier": "1",   "probability_weight": 20, "color": "#888888"},
        {"label": "2x",   "multiplier": "2",   "probability_weight": 20, "color": "#1A237E"},
        {"label": "5x",   "multiplier": "5",   "probability_weight": 10, "color": "#C9961A"}
      ]
    }
    """
    def get_permissions(self):
        if self.request.method == 'POST':
            return [CanEditRTP()]
        return [IsAdminUser()]

    def get(self, request):
        from apps.spin.models import Wheel, WheelSegment

        wheels = Wheel.objects.prefetch_related('segments').order_by('min_stake')
        data = []

        for wheel in wheels:
            segments = list(
                wheel.segments.filter(is_active=True).order_by('position')
            )

            # Compute house edge and actual RTP from segment weights
            total_weight = sum(s.probability_weight for s in segments) or 1
            expected_return = sum(
                (s.probability_weight / total_weight) * float(s.multiplier)
                for s in segments
            )
            computed_rtp = round(expected_return * 100, 2)
            house_edge = round(100 - computed_rtp, 2)

            # Who last modified this wheel
            data.append({
                'id': str(wheel.id),
                'name': wheel.name,
                'wheel_type': wheel.wheel_type,
                'currency_type': wheel.currency_type,
                'min_stake': str(wheel.min_stake),
                'max_stake': str(wheel.max_stake),
                'rtp_target': str(wheel.rtp_target or ''),
                'computed_rtp': str(computed_rtp),
                'house_edge': str(house_edge),
                'is_active': wheel.is_active,
                'is_welcome_only': wheel.is_welcome_only,
                'total_spins': wheel.spins.count() if hasattr(wheel, 'spins') else 0,
                'segments': [
                    {
                        'position': s.position,
                        'label': s.label,
                        'multiplier': str(s.multiplier),
                        'probability_weight': s.probability_weight,
                        'probability_pct': round(
                            (s.probability_weight / total_weight) * 100, 1
                        ),
                        'color': s.color,
                        'is_active': s.is_active,
                    }
                    for s in segments
                ],
            })

        return Response({'success': True, 'data': {'wheels': data, 'count': len(data)}})

    def post(self, request):
        from apps.spin.models import Wheel, WheelSegment

        body = request.data
        segments_data = body.get('segments', [])

        # Validate segments
        if not segments_data:
            return Response(
                {'error': True, 'code': 'MISSING_SEGMENTS',
                 'message': 'At least one segment is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        total_weight = sum(
            int(s.get('probability_weight', 0)) for s in segments_data
        )
        if total_weight <= 0:
            return Response(
                {'error': True, 'code': 'INVALID_WEIGHTS',
                 'message': 'Segments must have positive probability weights.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            min_stake = Decimal(str(body.get('min_stake', '0')))
            max_stake = Decimal(str(body.get('max_stake', '0')))
            rtp_target = Decimal(str(body.get('rtp_target', '80')))
        except Exception:
            return Response(
                {'error': True, 'code': 'INVALID_AMOUNTS',
                 'message': 'min_stake, max_stake, and rtp_target must be valid numbers.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            wheel = Wheel.objects.create(
                name=body.get('name', 'New Wheel'),
                wheel_type=body.get('wheel_type', 'standard'),
                currency_type=body.get('currency_type', 'coin'),
                min_stake=min_stake,
                max_stake=max_stake,
                rtp_target=rtp_target,
                is_active=bool(body.get('is_active', False)),
                is_welcome_only=bool(body.get('is_welcome_only', False)),
            )

            for i, seg in enumerate(segments_data):
                WheelSegment.objects.create(
                    wheel=wheel,
                    position=seg.get('position', i),
                    label=str(seg.get('label', '')),
                    multiplier=Decimal(str(seg.get('multiplier', '0'))),
                    probability_weight=int(seg.get('probability_weight', 0)),
                    color=str(seg.get('color', '#888888')),
                    is_active=True,
                )
        except Exception as e:
            logger.exception('Failed to create wheel')
            return Response(
                {'error': True, 'code': 'CREATE_FAILED', 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            'Wheel created by admin %s: %s',
            request.user.id, wheel.name,
        )

        return Response(
            {
                'success': True,
                'message': 'Wheel created successfully.',
                'data': {'id': str(wheel.id), 'name': wheel.name},
            },
            status=status.HTTP_201_CREATED,
        )


class RTPControlDetailView(APIView):
    """
    PUT /api/v1/admin/rtp/<wheel_id>/    Edit a wheel and/or its segments
    """
    def get_permissions(self):
        if self.request.method in ('PUT', 'PATCH', 'DELETE'):
            return [CanEditRTP()]
        return [IsAdminUser()]

    def put(self, request, wheel_id):
        from apps.spin.models import Wheel, WheelSegment

        try:
            wheel = Wheel.objects.get(id=wheel_id)
        except Wheel.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Wheel not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        body = request.data

        # Update wheel-level fields if present
        if 'name' in body:
            wheel.name = body['name']
        if 'is_active' in body:
            wheel.is_active = bool(body['is_active'])
        if 'rtp_target' in body:
            wheel.rtp_target = Decimal(str(body['rtp_target']))
        if 'min_stake' in body:
            wheel.min_stake = Decimal(str(body['min_stake']))
        if 'max_stake' in body:
            wheel.max_stake = Decimal(str(body['max_stake']))

        wheel.save()

        # Update segments if present
        segments_data = body.get('segments', [])
        for seg_data in segments_data:
            position = seg_data.get('position')
            if position is None:
                continue
            WheelSegment.objects.update_or_create(
                wheel=wheel,
                position=int(position),
                defaults={
                    'label': str(seg_data.get('label', '')),
                    'multiplier': Decimal(str(seg_data.get('multiplier', '0'))),
                    'probability_weight': int(seg_data.get('probability_weight', 0)),
                    'color': str(seg_data.get('color', '#888888')),
                    'is_active': bool(seg_data.get('is_active', True)),
                },
            )

        logger.info(
            'Wheel %s updated by admin %s', wheel_id, request.user.id
        )

        return Response({'success': True, 'message': 'Wheel updated successfully.'})


# ══════════════════════════════════════════════════════════════════════════════
# SCREEN 4 — USERS
# ══════════════════════════════════════════════════════════════════════════════

class AdminUsersListView(APIView):
    """
    GET /api/v1/admin/users/

    Overview stats + paginated user table.

    Query params:
      search    name or username
      filter    all | kyc_pending | flagged
      page      page number
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        from apps.kyc.models import KYCProfile

        # ── Overview stats ────────────────────────────────────────────
        total_users = User.objects.count()

        pending_kyc = KYCProfile.objects.exclude(
            personal_info_status='verified',
            bank_account_status='verified',
            document_status='verified',
        ).count()

        flagged_accounts = KYCProfile.objects.filter(
            Q(personal_info_status='rejected') |
            Q(bank_account_status='rejected') |
            Q(document_status='rejected')
        ).count()

        # ── Build query ───────────────────────────────────────────────
        qs = User.objects.select_related('kyc_profile').order_by('-created_at')

        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(username__icontains=search)
            )

        filter_by = request.query_params.get('filter', 'all')
        if filter_by == 'kyc_pending':
            qs = qs.filter(kyc_profile__isnull=False).exclude(
                kyc_profile__personal_info_status='verified',
                kyc_profile__bank_account_status='verified',
                kyc_profile__document_status='verified',
            )
        elif filter_by == 'flagged':
            qs = qs.filter(
                Q(kyc_profile__personal_info_status='rejected') |
                Q(kyc_profile__bank_account_status='rejected') |
                Q(kyc_profile__document_status='rejected')
            )

        # ── Paginate ──────────────────────────────────────────────────
        paginator = AdminPagination()
        page = paginator.paginate_queryset(qs, request)

        users_data = []
        for user in page:
            users_data.append({
                'id': user.id,
                'telegram_id': user.telegram_id,
                'name': format_user(user),
                'phone_number': getattr(user, 'phone_number', '') or '',
                'registered_on': user.created_at.strftime('%b %-d, %Y'),
                'registered_via': 'Telegram',
                'balance': str(
                    get_user_balance(user, 'cash') + get_user_balance(user, 'coin')
                ),
                'staked': str(get_user_balance(user, 'staked')),
                'kyc_status': get_kyc_display(user),
                'risk': get_risk_level(user),
                'is_active': user.is_active,
            })

        return Response({
            'success': True,
            'data': {
                'overview': {
                    'total_users': total_users,
                    'flagged_accounts': flagged_accounts,
                    'pending_kyc': pending_kyc,
                },
                **paginator.get_paginated_response_data(users_data),
            },
        })


class AdminUserDetailView(APIView):
    """
    GET /api/v1/admin/users/<user_id>/

    Full user profile card: name, ID, registered on, balance, staked, KYC, risk, bank.
    """
    permission_classes = [IsAdminUser]
    def get(self, request, user_id):
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'User not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ─── KYC ─────────────────────────────────────────────────────
        kyc_data = {'overall_status': 'unverified'}
        try:
            kyc = user.kyc_profile
            kyc_data = {
                'overall_status': kyc.overall_status,
                'display_status': get_kyc_display(user),
                'personal_info_status': kyc.personal_info_status,
                'bank_account_status': kyc.bank_account_status,
                'document_status': kyc.document_status,
                'personal_info_reason': kyc.personal_info_reason,
                'bank_account_reason': kyc.bank_account_reason,
                'document_reason': kyc.document_reason,
                'submitted_at': (
                    kyc.submitted_at.strftime('%b %-d, %Y')
                    if kyc.submitted_at else None
                ),
            }
        except Exception:
            pass

        # ─── Bank accounts (ALL active, not just one) ────────────────
        bank_accounts = []
        try:
            from apps.kyc.models import BankAccount
            for ba in BankAccount.objects.filter(user=user, is_active=True):
                bank_accounts.append({
                    'id': str(ba.id),
                    'bank_name': ba.bank_name,
                    'account_name': ba.account_name,
                    'account_number_masked': f'****{ba.account_number[-4:]}',
                    'is_default': ba.is_default,
                    'verified_at': (
                        ba.verified_at.strftime('%b %-d, %Y')
                        if ba.verified_at else None
                    ),
                })
        except Exception:
            pass

        # ─── Crypto wallets (v3 — new for Chunk 4) ───────────────────
        crypto_wallets = []
        try:
            from apps.withdrawals.models import CryptoWallet
            for cw in CryptoWallet.objects.filter(user=user, is_active=True):
                crypto_wallets.append({
                    'id': str(cw.id),
                    'network': cw.network,
                    'address_masked': f'{cw.address[:6]}...{cw.address[-6:]}',
                    'label': cw.label,
                    'is_default': cw.is_default,
                    'verified_at': (
                        cw.verified_at.strftime('%b %-d, %Y')
                        if cw.verified_at else None
                    ),
                })
        except Exception:
            pass

        # ─── Wallet (v3 5-balance shape) ─────────────────────────────
        wallet_summary = WalletService.get_wallet_summary(user)

        return Response({
            'success': True,
            'data': {
                'id': user.id,
                'telegram_id': user.telegram_id,
                'name': format_user(user),
                'username': user.username or '',
                'registered_on': user.created_at.strftime('%b %-d, %Y'),
                'registered_via': 'Telegram',
                'last_login': (
                    user.last_login.strftime('%b %-d, %Y %H:%M')
                    if user.last_login else None
                ),

                # v3: 5 spendable + withdrawable balances + staked
                'wallet': {
                    'crypto_coins': wallet_summary['crypto_coins'],
                    'naira_coins': wallet_summary['naira_coins'],
                    'bonus_coins': wallet_summary['bonus_coins'],
                    'crypto_withdraw_balance': wallet_summary['crypto_withdraw_balance'],
                    'naira_withdraw_balance': wallet_summary['naira_withdraw_balance'],
                    'staked': wallet_summary['staked'],
                },

                'kyc': kyc_data,
                'risk': get_risk_level(user),

                # Banking & crypto (v3)
                'bank_accounts': bank_accounts,
                'crypto_wallets': crypto_wallets,

                # Backward-compat: singular bank_account (the default one)
                'bank_account': bank_accounts[0] if bank_accounts else None,

                'is_active': user.is_active,
                'is_staff': user.is_staff,
            },
        })

    # def get(self, request, user_id):
    #     try:
    #         user = User.objects.get(id=user_id)
    #     except User.DoesNotExist:
    #         return Response(
    #             {'error': True, 'code': 'NOT_FOUND', 'message': 'User not found.'},
    #             status=status.HTTP_404_NOT_FOUND,
    #         )

    #     # KYC
    #     kyc_data = {'overall_status': 'unverified'}
    #     try:
    #         kyc = user.kyc_profile
    #         kyc_data = {
    #             'overall_status': kyc.overall_status,
    #             'display_status': get_kyc_display(user),
    #             'personal_info_status': kyc.personal_info_status,
    #             'bank_account_status': kyc.bank_account_status,
    #             'document_status': kyc.document_status,
    #             'personal_info_reason': kyc.personal_info_reason,
    #             'bank_account_reason': kyc.bank_account_reason,
    #             'document_reason': kyc.document_reason,
    #             'submitted_at': (
    #                 kyc.submitted_at.strftime('%b %-d, %Y')
    #                 if kyc.submitted_at else None
    #             ),
    #         }
    #     except Exception:
    #         pass

    #     # Active bank account
    #     bank_data = None
    #     try:
    #         from apps.kyc.models import BankAccount
    #         bank = BankAccount.objects.filter(user=user, is_active=True).first()
    #         if bank:
    #             bank_data = {
    #                 'bank_name': bank.bank_name,
    #                 'account_name': bank.account_name,
    #                 'account_number_masked': f'****{bank.account_number[-4:]}',
    #                 'verified_at': (
    #                     bank.verified_at.strftime('%b %-d, %Y')
    #                     if bank.verified_at else None
    #                 ),
    #             }
    #     except Exception:
    #         pass

    #     # cash = get_user_balance(user, 'cash')
    #     # coin = get_user_balance(user, 'coin')
    #     # staked = get_user_balance(user, 'staked')
    #     wallet_summary = WalletService.get_wallet_summary(user)

    #     return Response({
    #         'success': True,
    #         'data': {
    #             'id': user.id,
    #             'telegram_id': user.telegram_id,
    #             'name': format_user(user),
    #             'username': user.username or '',
    #             'registered_on': user.created_at.strftime('%b %-d, %Y'),
    #             'registered_via': 'Telegram',
    #             'last_login': (
    #                 user.last_login.strftime('%b %-d, %Y %H:%M')
    #                 if user.last_login else None
    #             ),
    #             'wallet': {
    #                 'deposit_coins': wallet_summary['deposit_coins'],
    #                 'bonus_coins': wallet_summary['bonus_coins'],
    #                 'total_coins': wallet_summary['total_coins'],
    #                 'earnings': wallet_summary['earnings'],
    #                 'earnings_usd_equivalent': wallet_summary['earnings_usd_equivalent'],
    #                 'staked': wallet_summary['staked'],
    #             },

    #             # 'cash_balance': str(cash),
    #             # 'coin_balance': str(coin),
    #             # 'total_balance': str(cash + coin),
    #             # 'staked': str(staked),
    #             # 'kyc': kyc_data,
    #             'risk': get_risk_level(user),
    #             'bank_account': bank_data,
    #             'is_active': user.is_active,
    #             'is_staff': user.is_staff,
    #         },
    #     })


class AdminUserSpinsView(APIView):
    """
    GET /api/v1/admin/users/<user_id>/spins/

    Paginated spin history for a specific user.

    Query params:
      outcome   win | loss | push | partial_loss
      page
    """
    permission_classes = [IsAdminUser]

    def get(self, request, user_id):
        from apps.spin.models import Spin

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'User not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        spins = Spin.objects.filter(user=user).select_related(
            'wheel', 'segment_landed'
        ).order_by('-created_at')

        # Filter by outcome if provided
        # ?outcome=win | ?outcome=loss | ?outcome=push | ?outcome=partial_loss
        outcome_filter = request.query_params.get('outcome', '').strip()
        if outcome_filter:
            spins = spins.filter(outcome=outcome_filter)

        paginator = AdminPagination()
        page = paginator.paginate_queryset(spins, request)

        spins_data = []
        for spin in page:
            spins_data.append({
                'id': str(spin.id),
                'wheel': spin.wheel.name if spin.wheel else '',
                'stake': str(spin.stake_amount),
                'result_label': (
                    spin.segment_landed.label
                    if spin.segment_landed else spin.outcome.upper()
                ),
                'multiplier': (
                    f"{spin.segment_landed.multiplier}x"
                    if spin.segment_landed else '0x'
                ),
                'outcome': spin.outcome,
                'win_value': str(spin.payout_amount),
                'is_welcome_spin': spin.is_welcome_spin,
                'date': spin.created_at.strftime('%b %-d, %Y'),
            })

        return Response({
            'success': True,
            'data': paginator.get_paginated_response_data(spins_data),
        })


class AdminUserTransactionsView(APIView):
    """
    GET /api/v1/admin/users/<user_id>/transactions/

    Paginated wallet transaction history for a specific user.

    Query params:
      type    deposit | withdrawal | spin_stake | spin_win | refund
      page
    """
    permission_classes = [IsAdminUser]

    def get(self, request, user_id):
        from apps.wallet.models import Transaction

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'User not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        txs = Transaction.objects.filter(user=user).order_by('-created_at')

        # Filter by type if provided
        # ?type=deposit | ?type=withdrawal | ?type=spin_stake | ?type=spin_win
        # spin_stake maps to DB type=stake, spin_win maps to DB type=win
        TYPE_FILTER_MAP = {
            'deposit': 'deposit',
            'withdrawal': 'withdrawal',
            'spin_stake': 'stake',
            'spin_win': 'win',
            'refund': 'refund',
        }
        type_filter = request.query_params.get('type', '').strip()
        if type_filter:
            db_type = TYPE_FILTER_MAP.get(type_filter, type_filter)
            txs = txs.filter(type=db_type)

        paginator = AdminPagination()
        page = paginator.paginate_queryset(txs, request)

        LABEL_MAP = {
            'deposit': 'Cash Deposit',
            'withdrawal': 'Cash Withdrawal',
            'win': 'Spin Win',
            'stake': 'Stake',
            'stake_release': 'Stake Return',
            'stake_forfeit': 'Stake Lost',
            'refund': 'Refund',
        }

        tx_data = []
        for tx in page:
            is_credit = float(tx.amount) >= 0
            tx_data.append({
                'id': str(tx.id),
                'type': tx.type,
                'label': LABEL_MAP.get(tx.type, tx.type.replace('_', ' ').title()),
                'amount': str(tx.amount),
                'is_credit': is_credit,
                'balance_type': tx.balance_type,
                'balance_before': str(tx.balance_before),
                'balance_after': str(tx.balance_after),
                'status': tx.status,
                'reference_id': tx.reference_id or '',
                'date': tx.created_at.strftime('%b %-d, %Y'),
            })

        return Response({
            'success': True,
            'data': paginator.get_paginated_response_data(tx_data),
        })


# ══════════════════════════════════════════════════════════════════════════════
# SCREEN 5 — WITHDRAWAL MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

class AdminWithdrawalsListView(APIView):
    """
    GET /api/v1/admin/withdrawals/

    Overview stats + paginated withdrawal table.

    Query params:
      search    user name
      status    pending_review | pending | processing | completed | failed | rejected
      risk      Low | Medium | High
      page
    """
    permission_classes = [IsAdminUser]
    def get(self, request):
        from apps.withdrawals.models import Withdrawal

        # ─── Overview stats — split per currency (D7.2) ─────────────────
        # NGN side
        ngn_qs = Withdrawal.objects.filter(currency=Withdrawal.Currency.NGN)
        ngn_pending = ngn_qs.filter(
            status__in=['pending_review', 'pending', 'processing'],
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
        ngn_paid = ngn_qs.filter(status='completed').aggregate(
            t=Sum('net_amount')
        )['t'] or Decimal('0')
        ngn_queued = ngn_qs.filter(status='pending_review').count()

        # USDT side
        usdt_qs = Withdrawal.objects.filter(currency=Withdrawal.Currency.USDT)
        usdt_pending = usdt_qs.filter(
            status__in=['pending_review', 'pending', 'processing'],
        ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
        usdt_paid = usdt_qs.filter(status='completed').aggregate(
            t=Sum('net_amount')
        )['t'] or Decimal('0')
        usdt_queued = usdt_qs.filter(status='pending_review').count()

        total_queued = ngn_queued + usdt_queued  # OK to sum COUNTS (not amounts)

        # ─── Build query with filters ───────────────────────────────────
        qs = Withdrawal.objects.select_related(
            'user', 'bank_account'
        ).order_by('-requested_at')

        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(user__first_name__icontains=search) |
                Q(user__last_name__icontains=search) |
                Q(user__username__icontains=search)
            )

        filter_status = request.query_params.get('status', '').strip()
        if filter_status:
            qs = qs.filter(status=filter_status)

        # NEW v3: filter by rail
        filter_rail = request.query_params.get('rail', '').strip()
        if filter_rail in ('bank', 'crypto'):
            qs = qs.filter(rail=filter_rail)

        # NEW v3: filter by currency
        filter_currency = request.query_params.get('currency', '').strip()
        if filter_currency in ('NGN', 'USDT'):
            qs = qs.filter(currency=filter_currency)

        # ─── Paginate ──────────────────────────────────────────────────
        paginator = AdminPagination()
        page = paginator.paginate_queryset(qs, request)

        def _amount_type(amount, currency):
            """
            Risk-bucket by amount, per currency. The thresholds differ
            because NGN and USDT have wildly different magnitudes.
            """
            if currency == 'NGN':
                if amount < Decimal('10000'):
                    return 'Small'
                elif amount < Decimal('50000'):
                    return 'Medium'
                return 'Large'
            else:  # USDT
                if amount < Decimal('20'):
                    return 'Small'
                elif amount < Decimal('100'):
                    return 'Medium'
                return 'Large'

        RISK_MAP = {'Small': 'Low', 'Medium': 'Medium', 'Large': 'High'}

        items = []
        for w in page:
            amt_type = _amount_type(w.amount, w.currency)

            # Rail-aware destination display
            if w.rail == 'bank':
                destination = w.bank_account.bank_name if w.bank_account else ''
                account_masked = (
                    f'****{w.bank_account.account_number[-4:]}'
                    if w.bank_account else ''
                )
            else:  # crypto
                destination = f'{w.network} USDT'
                account_masked = (
                    f'{w.wallet_address[:6]}...{w.wallet_address[-6:]}'
                    if w.wallet_address else ''
                )

            items.append({
                'id': str(w.id),
                'name': format_user(w.user),
                'user_id': w.user.id,

                # v3: rail + currency
                'rail': w.rail,
                'currency': w.currency,
                'amount': str(w.amount),
                'net_amount': str(w.net_amount),

                # Rail-aware destination
                'destination': destination,
                'account_masked': account_masked,

                # Crypto-only fields (empty for bank)
                'wallet_address': w.wallet_address,
                'network': w.network,
                'tx_hash': w.tx_hash,

                # Bank-only (kept for backward compat with FE)
                'bank': w.bank_account.bank_name if w.bank_account else '',

                'type': amt_type,
                'risk': RISK_MAP[amt_type],
                'status': w.status,
                'status_display': w.get_status_display(),
                'requires_review': w.requires_review,
                'forced_manual_review': w.forced_manual_review,
                'reference': w.reference,
                'failure_reason': w.failure_reason or '',
                'requested_at': w.requested_at.strftime('%b %-d, %Y %H:%M'),
                'completed_at': (
                    w.completed_at.strftime('%b %-d, %Y %H:%M')
                    if w.completed_at else None
                ),
            })

        return Response({
            'success': True,
            'data': {
                # v3: per-currency overview (NGN and USDT NEVER summed)
                'overview': {
                    'ngn': {
                        'total_pending': str(ngn_pending),
                        'total_paid': str(ngn_paid),
                        'queued': ngn_queued,
                    },
                    'usdt': {
                        'total_pending': str(usdt_pending),
                        'total_paid': str(usdt_paid),
                        'queued': usdt_queued,
                    },
                    'queued_total': total_queued,
                },
                **paginator.get_paginated_response_data(items),
            },
        })

    # def get(self, request):
    #     from apps.withdrawals.models import Withdrawal

    #     # ── Overview stats ────────────────────────────────────────────
    #     total_pending_amount = Withdrawal.objects.filter(
    #         status__in=['pending_review', 'pending', 'processing'],
    #     ).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    #     total_paid_amount = Withdrawal.objects.filter(
    #         status='completed',
    #     ).aggregate(t=Sum('net_amount'))['t'] or Decimal('0')

    #     queued_count = Withdrawal.objects.filter(
    #         status='pending_review',
    #     ).count()

    #     # ── Build query ───────────────────────────────────────────────
    #     qs = Withdrawal.objects.select_related(
    #         'user', 'bank_account'
    #     ).order_by('-requested_at')

    #     search = request.query_params.get('search', '').strip()
    #     if search:
    #         qs = qs.filter(
    #             Q(user__first_name__icontains=search) |
    #             Q(user__last_name__icontains=search) |
    #             Q(user__username__icontains=search)
    #         )

    #     filter_status = request.query_params.get('status', '').strip()
    #     if filter_status:
    #         qs = qs.filter(status=filter_status)

    #     # ── Paginate ──────────────────────────────────────────────────
    #     paginator = AdminPagination()
    #     page = paginator.paginate_queryset(qs, request)

    #     def _amount_type(amount):
    #         if amount < Decimal('10000'):
    #             return 'Small'
    #         elif amount < Decimal('50000'):
    #             return 'Medium'
    #         return 'Large'

    #     RISK_MAP = {'Small': 'Low', 'Medium': 'Medium', 'Large': 'High'}

    #     items = []
    #     for w in page:
    #         amt_type = _amount_type(w.amount)
    #         items.append({
    #             'id': str(w.id),
    #             'name': format_user(w.user),
    #             'user_id': w.user.id,
    #             'amount': str(w.amount),
    #             'net_amount': str(w.net_amount),
    #             'bank': w.bank_account.bank_name if w.bank_account else '',
    #             'account_masked': (
    #                 f'****{w.bank_account.account_number[-4:]}'
    #                 if w.bank_account else ''
    #             ),
    #             'type': amt_type,
    #             'risk': RISK_MAP[amt_type],
    #             'status': w.status,
    #             'status_display': w.get_status_display(),
    #             'requires_review': w.requires_review,
    #             'forced_manual_review': w.forced_manual_review,
    #             'reference': w.reference,
    #             'failure_reason': w.failure_reason or '',
    #             'requested_at': w.requested_at.strftime('%b %-d, %Y %H:%M'),
    #             'completed_at': (
    #                 w.completed_at.strftime('%b %-d, %Y %H:%M')
    #                 if w.completed_at else None
    #             ),
    #         })

    #     return Response({
    #         'success': True,
    #         'data': {
    #             'overview': {
    #                 'total_pending': str(total_pending_amount),
    #                 'total_paid': str(total_paid_amount),
    #                 'queued': queued_count,
    #             },
    #             **paginator.get_paginated_response_data(items),
    #         },
    #     })


class AdminWithdrawalApproveView(APIView):
    """
    POST /api/v1/admin/withdrawals/<withdrawal_id>/approve/

    Body (optional): { "notes": "Verified manually" }
    """
    permission_classes = [CanApproveWithdrawal]

    def post(self, request, withdrawal_id):
        from apps.withdrawals.models import Withdrawal
        from apps.withdrawals.services import WithdrawalService, WithdrawalServiceError

        try:
            withdrawal = Withdrawal.objects.get(id=withdrawal_id)
        except Withdrawal.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Withdrawal not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            WithdrawalService.approve(
                withdrawal,
                request.user,
                notes=request.data.get('notes', ''),
            )
        except WithdrawalServiceError as e:
            return Response(
                {'error': True, 'code': 'CANNOT_APPROVE', 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            'Withdrawal %s approved by admin %s', withdrawal_id, request.user.id
        )
        return Response({
            'success': True,
            'message': 'Withdrawal approved. Processing payout.',
        })


class AdminWithdrawalRejectView(APIView):
    """
    POST /api/v1/admin/withdrawals/<withdrawal_id>/reject/

    Body (required): { "reason": "Suspicious activity detected" }
    """
    permission_classes = [CanRejectWithdrawal]

    def post(self, request, withdrawal_id):
        from apps.withdrawals.models import Withdrawal
        from apps.withdrawals.services import WithdrawalService, WithdrawalServiceError

        try:
            withdrawal = Withdrawal.objects.get(id=withdrawal_id)
        except Withdrawal.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Withdrawal not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        reason = request.data.get('reason', '').strip()
        if not reason:
            return Response(
                {'error': True, 'code': 'REASON_REQUIRED',
                 'message': 'A rejection reason is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            WithdrawalService.reject(withdrawal, request.user, reason=reason)
        except WithdrawalServiceError as e:
            return Response(
                {'error': True, 'code': 'CANNOT_REJECT', 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            'Withdrawal %s rejected by admin %s', withdrawal_id, request.user.id
        )
        return Response({
            'success': True,
            'message': 'Withdrawal rejected. Funds returned to user wallet.',
        })


# ══════════════════════════════════════════════════════════════════════════════
# SCREEN 6 — KYC REVIEW QUEUE
# ══════════════════════════════════════════════════════════════════════════════

class AdminKYCQueueView(APIView):
    """
    GET /api/v1/admin/kyc/queue/

    Overview stats + paginated KYC submissions needing review.

    Query params:
      search    user name or full name
      status    pending | requires_correction | rejected | approved
      page
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        from apps.kyc.models import BankAccount, KYCDocument, KYCProfile

        # ── Overview ──────────────────────────────────────────────────
        total_pending = KYCProfile.objects.filter(
            Q(personal_info_status__in=['pending', 'requires_correction']) |
            Q(bank_account_status__in=['pending', 'requires_correction']) |
            Q(document_status__in=['pending', 'requires_correction'])
        ).count()

        total_approved = KYCProfile.objects.filter(
            personal_info_status='verified',
            bank_account_status='verified',
            document_status='verified',
        ).count()

        total_rejected = KYCProfile.objects.filter(
            Q(personal_info_status='rejected') |
            Q(bank_account_status='rejected') |
            Q(document_status='rejected')
        ).count()

        # ── Build query ───────────────────────────────────────────────
        qs = KYCProfile.objects.select_related('user').order_by('-submitted_at')

        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(full_name__icontains=search) |
                Q(user__first_name__icontains=search) |
                Q(user__last_name__icontains=search)
            )

        filter_status = request.query_params.get('status', '').strip()
        if filter_status == 'pending':
            qs = qs.filter(
                Q(personal_info_status='pending') |
                Q(bank_account_status='pending') |
                Q(document_status='pending')
            )
        elif filter_status == 'requires_correction':
            qs = qs.filter(
                Q(personal_info_status='requires_correction') |
                Q(bank_account_status='requires_correction') |
                Q(document_status='requires_correction')
            )
        elif filter_status == 'rejected':
            qs = qs.filter(
                Q(personal_info_status='rejected') |
                Q(bank_account_status='rejected') |
                Q(document_status='rejected')
            )
        elif filter_status == 'approved':
            qs = qs.filter(
                personal_info_status='verified',
                bank_account_status='verified',
                document_status='verified',
            )

        # ── Paginate ──────────────────────────────────────────────────
        paginator = AdminPagination()
        page = paginator.paginate_queryset(qs, request)

        items = []
        for kyc in page:
            # Get latest active document
            doc = KYCDocument.objects.filter(
                user=kyc.user,
            ).exclude(
                status=KYCDocument.Status.REJECTED
            ).order_by('-uploaded_at').first()

            # Get active bank account
            bank = BankAccount.objects.filter(
                user=kyc.user, is_active=True
            ).first()

            doc_url = None
            try:
                if doc and doc.file:
                    doc_url = doc.file.url
            except Exception:
                pass

            items.append({
                'id': str(kyc.id),
                'user_id': kyc.user.id,
                'telegram_id': kyc.user.telegram_id,
                'name': kyc.full_name or format_user(kyc.user),
                'overall_status': kyc.overall_status,
                'can_withdraw': kyc.can_withdraw,
                'sections': {
                    'personal_info': {
                        'status': kyc.personal_info_status,
                        'reason': kyc.personal_info_reason,
                    },
                    'bank_account': {
                        'status': kyc.bank_account_status,
                        'reason': kyc.bank_account_reason,
                        'bank_name': bank.bank_name if bank else None,
                        'account_name': bank.account_name if bank else None,
                        'account_masked': (
                            f'****{bank.account_number[-4:]}' if bank else None
                        ),
                    },
                    'document': {
                        'status': kyc.document_status,
                        'reason': kyc.document_reason,
                        'filename': doc.original_filename if doc else None,
                        'document_type': doc.document_type if doc else None,
                        'file_url': doc_url,
                    },
                },
                'submitted_at': (
                    kyc.submitted_at.strftime('%b %-d, %Y')
                    if kyc.submitted_at else None
                ),
                'last_resubmission_at': (
                    kyc.last_resubmission_at.strftime('%b %-d, %Y')
                    if kyc.last_resubmission_at else None
                ),
            })

        return Response({
            'success': True,
            'data': {
                'overview': {
                    'total_pending': total_pending,
                    'total_approved': total_approved,
                    'total_rejected': total_rejected,
                },
                **paginator.get_paginated_response_data(items),
            },
        })


class AdminKYCApproveView(APIView):
    """
    POST /api/v1/admin/kyc/<kyc_id>/approve/

    Body: { "section": "personal_info" | "bank_account" | "document" | "all" }
    """
    permission_classes = [CanApproveKYC]

    def post(self, request, kyc_id):
        from apps.kyc.models import KYCProfile

        try:
            kyc = KYCProfile.objects.get(id=kyc_id)
        except KYCProfile.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'KYC profile not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        section = request.data.get('section', 'all')
        valid = ['personal_info', 'bank_account', 'document', 'all']
        if section not in valid:
            return Response(
                {'error': True, 'code': 'INVALID_SECTION',
                 'message': f'Section must be one of: {valid}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        V = KYCProfile.SectionStatus.VERIFIED
        if section in ('all', 'personal_info'):
            kyc.personal_info_status = V
            kyc.personal_info_reason = ''
        if section in ('all', 'bank_account'):
            kyc.bank_account_status = V
            kyc.bank_account_reason = ''
        if section in ('all', 'document'):
            kyc.document_status = V
            kyc.document_reason = ''

        kyc.reviewed_at = timezone.now()
        kyc.save()
        NotificationService.send_async(
            telegram_id=profile.user.telegram_id,
            notification_type='kyc_approved',
            data={},
        )

        logger.info(
            'KYC %s section=%s approved by admin %s',
            kyc_id, section, request.user.id,
        )

        return Response({
            'success': True,
            'message': f'KYC {section} approved.',
            'data': {
                'overall_status': kyc.overall_status,
                'can_withdraw': kyc.can_withdraw,
            },
        })


class AdminKYCRejectView(APIView):
    """
    POST /api/v1/admin/kyc/<kyc_id>/reject/

    Body: {
      "section": "personal_info" | "bank_account" | "document",
      "reason": "Document appears to be edited"
    }
    """
    permission_classes = [CanRejectKYC]

    def post(self, request, kyc_id):
        from apps.kyc.models import KYCProfile

        try:
            kyc = KYCProfile.objects.get(id=kyc_id)
        except KYCProfile.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'KYC profile not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        section = request.data.get('section', '').strip()
        reason = request.data.get('reason', '').strip()

        if section not in ['personal_info', 'bank_account', 'document']:
            return Response(
                {'error': True, 'code': 'INVALID_SECTION',
                 'message': 'section must be personal_info, bank_account, or document'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not reason:
            return Response(
                {'error': True, 'code': 'REASON_REQUIRED',
                 'message': 'A rejection reason is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        R = KYCProfile.SectionStatus.REJECTED
        if section == 'personal_info':
            kyc.personal_info_status = R
            kyc.personal_info_reason = reason
        elif section == 'bank_account':
            kyc.bank_account_status = R
            kyc.bank_account_reason = reason
        elif section == 'document':
            kyc.document_status = R
            kyc.document_reason = reason

        kyc.reviewed_at = timezone.now()
        kyc.save()

        NotificationService.send_async(
            telegram_id=profile.user.telegram_id,
            notification_type='kyc_rejected',
            data={'reason': reason or 'Please review and resubmit your KYC.'},
        )

        logger.info(
            'KYC %s section=%s rejected by admin %s',
            kyc_id, section, request.user.id,
        )

        return Response({
            'success': True,
            'message': f'KYC {section} rejected. User notified.',
            'data': {
                'overall_status': kyc.overall_status,
                'can_withdraw': kyc.can_withdraw,
            },
        })


# ══════════════════════════════════════════════════════════════════════════════
# SCREEN 7 — FRAUD & RISK MONITOR
# ══════════════════════════════════════════════════════════════════════════════

class AdminFraudMonitorView(APIView):
    """
    GET /api/v1/admin/fraud/

    Flags users with risk indicators:
    - High withdrawal frequency (>2 in 7 days)
    - Large single wins (>₦50,000)
    - KYC rejected
    - Multiple rapid spin sessions
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        from apps.kyc.models import KYCProfile
        from apps.spin.models import Spin
        from apps.withdrawals.models import Withdrawal

        now = timezone.now()
        seven_days_ago = now - timedelta(days=7)
        thirty_days_ago = now - timedelta(days=30)

        flagged = []
        seen = set()

        # High-frequency withdrawers
        high_freq = (
            Withdrawal.objects.filter(
                requested_at__gte=seven_days_ago,
                status__in=['pending_review', 'pending', 'processing', 'completed'],
            )
            .values('user_id')
            .annotate(count=Count('id'))
            .filter(count__gt=2)
        )
        for row in high_freq:
            uid = row['user_id']
            if uid in seen:
                continue
            seen.add(uid)
            try:
                u = User.objects.get(id=uid)
                flagged.append({
                    'user_id': uid,
                    'name': format_user(u),
                    'telegram_id': u.telegram_id,
                    'flag': f'High withdrawal frequency ({row["count"]} in 7 days)',
                    'risk': 'High',
                    'flag_type': 'withdrawal_frequency',
                })
            except User.DoesNotExist:
                pass

        # Large single wins
        large_wins = (
            Spin.objects.filter(
                outcome='win',
                payout_amount__gt=50000,
                created_at__gte=thirty_days_ago,
            )
            .select_related('user')
            .order_by('-payout_amount')[:30]
        )
        for spin in large_wins:
            uid = spin.user_id
            if uid in seen:
                continue
            seen.add(uid)
            flagged.append({
                'user_id': uid,
                'name': format_user(spin.user),
                'telegram_id': spin.user.telegram_id,
                'flag': f'Large win ₦{spin.payout_amount:,.0f}',
                'risk': 'Medium',
                'flag_type': 'large_win',
            })

        # KYC rejected
        rejected_kyc = KYCProfile.objects.filter(
            Q(personal_info_status='rejected') |
            Q(bank_account_status='rejected') |
            Q(document_status='rejected')
        ).select_related('user')[:30]

        for kyc in rejected_kyc:
            uid = kyc.user_id
            if uid in seen:
                continue
            seen.add(uid)
            flagged.append({
                'user_id': uid,
                'name': kyc.full_name or format_user(kyc.user),
                'telegram_id': kyc.user.telegram_id,
                'flag': 'KYC section rejected',
                'risk': 'High',
                'flag_type': 'kyc_rejected',
            })

        # Sort: High risk first, then Medium
        risk_order = {'High': 0, 'Medium': 1, 'Low': 2}
        flagged.sort(key=lambda x: risk_order.get(x['risk'], 3))

        return Response({
            'success': True,
            'data': {
                'total_flagged': len(flagged),
                'flagged_users': flagged,
            },
        })


# ══════════════════════════════════════════════════════════════════════════════
# SCREEN 8 — AUDIT LOGS
# ══════════════════════════════════════════════════════════════════════════════

class AdminAuditLogsView(APIView):
    """
    GET /api/v1/admin/audit-logs/

    Chronological log of admin actions:
    - Withdrawal approvals/rejections
    - KYC approvals/rejections
    - Wheel (RTP) changes

    Query params:
      type    withdrawal | kyc | rtp
      page
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        from apps.kyc.models import KYCProfile
        from apps.withdrawals.models import Withdrawal

        filter_type = request.query_params.get('type', '').strip()
        logs = []

        # ── Withdrawal actions ────────────────────────────────────────
        if not filter_type or filter_type == 'withdrawal':
            reviewed_wds = Withdrawal.objects.filter(
                reviewed_at__isnull=False,
            ).select_related('user', 'reviewed_by').order_by('-reviewed_at')[:100]

            for w in reviewed_wds:
                action = (
                    'approved' if w.status in ('completed', 'processing', 'pending')
                    else 'rejected'
                )
                logs.append({
                    'id': str(w.id),
                    'type': 'withdrawal',
                    'action': f'Withdrawal {action}',
                    'detail': f'₦{w.amount:,.0f} → {w.bank_account.bank_name if w.bank_account else ""}',
                    'target_user': format_user(w.user),
                    'target_user_id': w.user.id,
                    'performed_by': (
                        format_user(w.reviewed_by)
                        if w.reviewed_by else 'System'
                    ),
                    'notes': w.review_notes or '',
                    'timestamp': w.reviewed_at.isoformat(),
                    'timestamp_display': w.reviewed_at.strftime('%b %-d, %Y %H:%M'),
                })

        # ── KYC actions ────────────────────────────────────────────────
        if not filter_type or filter_type == 'kyc':
            reviewed_kyc = KYCProfile.objects.filter(
                reviewed_at__isnull=False,
            ).select_related('user').order_by('-reviewed_at')[:100]

            for kyc in reviewed_kyc:
                logs.append({
                    'id': str(kyc.id),
                    'type': 'kyc',
                    'action': f'KYC {kyc.overall_status}',
                    'detail': f'Overall: {kyc.overall_status}',
                    'target_user': kyc.full_name or format_user(kyc.user),
                    'target_user_id': kyc.user.id,
                    'performed_by': 'Admin',
                    'notes': '',
                    'timestamp': kyc.reviewed_at.isoformat(),
                    'timestamp_display': kyc.reviewed_at.strftime('%b %-d, %Y %H:%M'),
                })

        # ── Sort all by timestamp desc ─────────────────────────────────
        logs.sort(key=lambda x: x['timestamp'], reverse=True)
        logs = logs[:200]

        paginator = AdminPagination()
        page = paginator.paginate_queryset(logs, request)

        return Response({
            'success': True,
            'data': paginator.get_paginated_response_data(page),
        })