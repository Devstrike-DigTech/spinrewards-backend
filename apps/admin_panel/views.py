# """
# Admin Dashboard API views.

# All endpoints require is_staff or is_superuser.

# Screens covered:
#   1. Dashboard overview
#   2. Financials
#   3. RTP Control (list + create + edit)
#   4. Users (list + detail + spins + transactions)
#   5. Withdrawal Management (list + approve + reject)
#   6. KYC Review Queue (list + approve + reject)
#   7. Audit Logs
# """
# import logging
# from datetime import timedelta
# from decimal import Decimal

# from django.contrib.auth import get_user_model
# from django.db.models import Count, Q, Sum
# from django.db.models.functions import TruncMonth
# from django.utils import timezone
# from rest_framework import status
# from rest_framework.permissions import AllowAny
# from rest_framework_simplejwt.tokens import RefreshToken
# from rest_framework.pagination import PageNumberPagination
# from rest_framework.response import Response
# from rest_framework.views import APIView

# from .permissions import IsAdminUser

# logger = logging.getLogger(__name__)
# User = get_user_model()


# # ─── Pagination ────────────────────────────────────────────────────────────

# class AdminPagination(PageNumberPagination):
#     page_size = 20
#     page_size_query_param = 'page_size'
#     max_page_size = 100


# # ─── Helpers ───────────────────────────────────────────────────────────────

# def _risk_level(user) -> str:
#     """
#     Compute a simple risk level for a user.

#     Low   — KYC approved, no flags
#     Medium — KYC pending or < 7 days old
#     High  — KYC rejected, or flagged
#     """
#     try:
#         kyc = user.kyc_profile
#         kyc_status = kyc.overall_status
#     except Exception:
#         kyc_status = 'unverified'

#     if kyc_status == 'rejected':
#         return 'High'
#     if kyc_status in ('pending', 'partial', 'unverified'):
#         return 'Medium'

#     account_age = (timezone.now() - user.date_joined).days
#     if account_age < 7:
#         return 'Medium'

#     return 'Low'


# def _kyc_status_display(user) -> str:
#     try:
#         return user.kyc_profile.overall_status.capitalize()
#     except Exception:
#         return 'Pending'


# def _user_balance(user) -> Decimal:
#     """Get user's cash + coin balance."""
#     try:
#         from apps.wallet.services import WalletService
#         cash = WalletService.get_balance(user, 'cash')
#         coin = WalletService.get_balance(user, 'coin')
#         return cash + coin
#     except Exception:
#         return Decimal('0')


# def _user_staked(user) -> Decimal:
#     try:
#         from apps.wallet.services import WalletService
#         return WalletService.get_balance(user, 'staked')
#     except Exception:
#         return Decimal('0')


# def _monthly_data(queryset, date_field='created_at', value_field=None, months=12):
#     """
#     Aggregate queryset by month for the last N months.
#     Returns list of {'month': 'Jan', 'value': Decimal}.
#     """
#     from django.db.models.functions import TruncMonth
#     start = timezone.now() - timedelta(days=months * 30)

#     qs = queryset.filter(**{f'{date_field}__gte': start})

#     if value_field:
#         qs = qs.annotate(month=TruncMonth(date_field)).values('month').annotate(
#             total=Sum(value_field)
#         ).order_by('month')
#     else:
#         qs = qs.annotate(month=TruncMonth(date_field)).values('month').annotate(
#             total=Count('id')
#         ).order_by('month')

#     result = []
#     for row in qs:
#         result.append({
#             'month': row['month'].strftime('%b') if row['month'] else '',
#             'year': row['month'].year if row['month'] else '',
#             'value': str(row['total'] or 0),
#         })
#     return result


# # ─── Screen 1: Dashboard ───────────────────────────────────────────────────

# class DashboardView(APIView):
#     """
#     GET /api/v1/admin/dashboard/

#     Main dashboard overview:
#     - KPI cards: Total Revenue, Net Profit, Current RTP, Active Users
#     - Profit trend (monthly bar chart)
#     - Recent spins table
#     - Top winners
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request):
#         from apps.wallet.models import Transaction
#         from apps.spin.models import Spin

#         # ── KPI cards ──────────────────────────────────────────────────
#         # Total Revenue = sum of all deposits
#         total_deposits = Transaction.objects.filter(
#             type='deposit', status='completed', balance_type='coin',
#         ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

#         # Total Withdrawals paid out
#         total_withdrawals = Transaction.objects.filter(
#             type='withdrawal', status='completed',
#         ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
#         # withdrawal amounts stored as negative
#         total_withdrawals = abs(total_withdrawals)

#         net_profit = total_deposits - total_withdrawals

#         # Active users — logged in within last 30 days
#         thirty_days_ago = timezone.now() - timedelta(days=30)
#         active_users = User.objects.filter(
#             last_login__gte=thirty_days_ago
#         ).count()

#         # New users today
#         today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
#         new_today = User.objects.filter(date_joined__gte=today_start).count()

#         # Current RTP — computed from recent spins
#         # RTP = (total_winnings / total_staked) * 100
#         recent_spins = Spin.objects.filter(
#             created_at__gte=thirty_days_ago
#         )
#         total_staked = recent_spins.aggregate(
#             total=Sum('stake_amount')
#         )['total'] or Decimal('1')
#         total_won = recent_spins.filter(
#             outcome='win'
#         ).aggregate(total=Sum('payout_amount'))['total'] or Decimal('0')
#         current_rtp = round((total_won / total_staked) * 100, 1) if total_staked else 0

#         # ── Profit trend (monthly) ──────────────────────────────────────
#         profit_trend = _monthly_data(
#             Transaction.objects.filter(type='deposit', status='completed'),
#             value_field='amount',
#         )

#         # ── Recent spins ──────────────────────────────────────────────
#         recent_spins_data = []
#         for spin in Spin.objects.select_related('user', 'segment_landed').order_by(
#             '-created_at'
#         )[:10]:
#             recent_spins_data.append({
#                 'user': spin.user.get_full_name() or spin.user.username or f'User #{spin.user.telegram_id}',
#                 'stake': str(spin.stake_amount),
#                 'result': spin.segment_landed.label if spin.segment_landed else spin.outcome,
#                 'win_value': str(spin.payout_amount),
#                 'outcome': spin.outcome,
#                 'date': spin.created_at.strftime('%b %-d, %Y'),
#             })

#         # ── Top winners ────────────────────────────────────────────────
#         top_winners = []
#         winner_qs = Spin.objects.filter(
#             outcome='win',
#         ).values('user__id').annotate(
#             total_won=Sum('payout_amount')
#         ).order_by('-total_won')[:10]

#         for row in winner_qs:
#             try:
#                 u = User.objects.get(id=row['user__id'])
#                 top_winners.append({
#                     'user': u.get_full_name() or u.username or f'User #{u.telegram_id}',
#                     'win_value': str(row['total_won']),
#                 })
#             except User.DoesNotExist:
#                 pass

#         return Response({
#             'success': True,
#             'data': {
#                 'kpis': {
#                     'total_revenue': str(total_deposits),
#                     'net_profit': str(net_profit),
#                     'current_rtp': f'{current_rtp}%',
#                     'active_users': active_users,
#                     'new_users_today': new_today,
#                 },
#                 'profit_trend': profit_trend,
#                 'recent_spins': recent_spins_data,
#                 'top_winners': top_winners,
#             },
#         })


# # ─── Screen 2: Financials ──────────────────────────────────────────────────

# class FinancialsView(APIView):
#     """
#     GET /api/v1/admin/financials/

#     Financial overview:
#     - KPI cards: Total Deposits, Total Withdrawals, Pending Withdrawals
#     - Recent spins breakdown (staked/fees/ads donut chart)
#     - Cash flow (monthly line chart)
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request):
#         from apps.wallet.models import Transaction
#         from apps.withdrawals.models import Withdrawal
#         from apps.spin.models import Spin

#         # ── KPI cards ──────────────────────────────────────────────────
#         total_deposits = Transaction.objects.filter(
#             type='deposit', status='completed',
#         ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

#         total_withdrawals_paid = Withdrawal.objects.filter(
#             status='completed',
#         ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

#         pending_withdrawals = Withdrawal.objects.filter(
#             status__in=['pending_review', 'pending', 'processing'],
#         ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

#         # ── Spins breakdown (for donut chart) ─────────────────────────
#         thirty_days_ago = timezone.now() - timedelta(days=30)
#         spins_qs = Spin.objects.filter(created_at__gte=thirty_days_ago)

#         total_staked_spins = spins_qs.aggregate(
#             total=Sum('stake_amount')
#         )['total'] or Decimal('0')
#         total_won_spins = spins_qs.filter(
#             outcome='win'
#         ).aggregate(total=Sum('payout_amount'))['total'] or Decimal('0')
#         total_fees = total_staked_spins - total_won_spins  # house edge

#         # ── Cash flow (monthly) ────────────────────────────────────────
#         cash_flow = _monthly_data(
#             Transaction.objects.filter(type='deposit', status='completed'),
#             value_field='amount',
#         )

#         # Percent changes (vs last month) — simplified
#         last_month_start = (timezone.now().replace(day=1) - timedelta(days=1)).replace(day=1)
#         this_month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0)

#         deposits_this_month = Transaction.objects.filter(
#             type='deposit', status='completed',
#             created_at__gte=this_month_start,
#         ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

#         deposits_last_month = Transaction.objects.filter(
#             type='deposit', status='completed',
#             created_at__gte=last_month_start,
#             created_at__lt=this_month_start,
#         ).aggregate(total=Sum('amount'))['total'] or Decimal('1')

#         deposit_change = round(
#             ((deposits_this_month - deposits_last_month) / deposits_last_month) * 100, 1
#         ) if deposits_last_month else 0

#         return Response({
#             'success': True,
#             'data': {
#                 'kpis': {
#                     'total_deposits': str(total_deposits),
#                     'total_withdrawals': str(total_withdrawals_paid),
#                     'pending_withdrawals': str(pending_withdrawals),
#                     'deposit_change_pct': str(deposit_change),
#                 },
#                 'spins_breakdown': {
#                     'total_staked': str(total_staked_spins),
#                     'total_won': str(total_won_spins),
#                     'house_fees': str(total_fees),
#                 },
#                 'cash_flow': cash_flow,
#             },
#         })


# # ─── Screen 3: RTP Control ─────────────────────────────────────────────────

# class RTPListCreateView(APIView):
#     """
#     GET  /api/v1/admin/rtp/           List all wheel configurations
#     POST /api/v1/admin/rtp/           Create a new wheel/stake config
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request):
#         from apps.spin.models import Wheel, WheelSegment

#         wheels = Wheel.objects.prefetch_related('segments').all().order_by('min_stake')
#         data = []
#         for wheel in wheels:
#             segments = wheel.segments.filter(is_active=True).order_by('position')
#             # Compute house edge from segments
#             total_weight = sum(s.probability_weight for s in segments)
#             expected_return = sum(
#                 (s.probability_weight / total_weight) * float(s.multiplier)
#                 for s in segments
#             ) if total_weight else 0
#             house_edge = round((1 - expected_return) * 100, 1)

#             data.append({
#                 'id': str(wheel.id),
#                 'name': wheel.name,
#                 'wheel_type': wheel.wheel_type,
#                 'min_stake': str(wheel.min_stake),
#                 'max_stake': str(wheel.max_stake),
#                 'rtp_target': str(wheel.rtp_target),
#                 'computed_rtp': str(wheel.computed_rtp) if hasattr(wheel, 'computed_rtp') else None,
#                 'house_edge': str(house_edge),
#                 'is_active': wheel.is_active,
#                 'currency_type': wheel.currency_type,
#                 'segments': [
#                     {
#                         'position': s.position,
#                         'label': s.label,
#                         'multiplier': str(s.multiplier),
#                         'probability_weight': s.probability_weight,
#                         'color': s.color,
#                         'is_active': s.is_active,
#                     }
#                     for s in segments
#                 ],
#             })

#         return Response({'success': True, 'data': {'wheels': data}})

#     def post(self, request):
#         """
#         Create a new wheel with segments.

#         Body:
#         {
#           "name": "Entry Stake",
#           "wheel_type": "standard",
#           "min_stake": "200",
#           "max_stake": "500",
#           "rtp_target": "80",
#           "is_active": false,
#           "segments": [
#             {"label": "Loss", "multiplier": "0", "probability_weight": 30, "color": "#3a3a3a"},
#             {"label": "0.5x", "multiplier": "0.5", "probability_weight": 20, "color": "#5a5a5a"},
#             ...
#           ]
#         }
#         """
#         from apps.spin.models import Wheel, WheelSegment
#         from decimal import Decimal

#         data = request.data
#         segments_data = data.get('segments', [])

#         if not segments_data:
#             return Response(
#                 {'error': True, 'code': 'MISSING_SEGMENTS',
#                  'message': 'At least one segment is required.'},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         try:
#             wheel = Wheel.objects.create(
#                 name=data.get('name', 'New Wheel'),
#                 wheel_type=data.get('wheel_type', 'standard'),
#                 currency_type=data.get('currency_type', 'coin'),
#                 min_stake=Decimal(str(data.get('min_stake', '0'))),
#                 max_stake=Decimal(str(data.get('max_stake', '0'))),
#                 rtp_target=Decimal(str(data.get('rtp_target', '80'))),
#                 is_active=data.get('is_active', False),
#                 is_welcome_only=data.get('is_welcome_only', False),
#             )

#             for i, seg in enumerate(segments_data):
#                 WheelSegment.objects.create(
#                     wheel=wheel,
#                     position=seg.get('position', i),
#                     label=seg.get('label', ''),
#                     multiplier=Decimal(str(seg.get('multiplier', '0'))),
#                     probability_weight=int(seg.get('probability_weight', 0)),
#                     color=seg.get('color', '#888888'),
#                     is_active=True,
#                 )
#         except Exception as e:
#             return Response(
#                 {'error': True, 'code': 'CREATE_FAILED', 'message': str(e)},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         return Response(
#             {'success': True, 'message': 'Wheel created.', 'data': {'id': str(wheel.id)}},
#             status=status.HTTP_201_CREATED,
#         )


# class RTPDetailView(APIView):
#     """
#     PUT /api/v1/admin/rtp/<wheel_id>/    Update wheel and/or its segments
#     """
#     permission_classes = [IsAdminUser]

#     def put(self, request, wheel_id):
#         from apps.spin.models import Wheel, WheelSegment
#         from decimal import Decimal

#         try:
#             wheel = Wheel.objects.get(id=wheel_id)
#         except Wheel.DoesNotExist:
#             return Response(
#                 {'error': True, 'code': 'NOT_FOUND', 'message': 'Wheel not found.'},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         data = request.data

#         # Update wheel fields
#         if 'name' in data:
#             wheel.name = data['name']
#         if 'rtp_target' in data:
#             wheel.rtp_target = Decimal(str(data['rtp_target']))
#         if 'is_active' in data:
#             wheel.is_active = data['is_active']
#         if 'min_stake' in data:
#             wheel.min_stake = Decimal(str(data['min_stake']))
#         if 'max_stake' in data:
#             wheel.max_stake = Decimal(str(data['max_stake']))
#         wheel.save()

#         # Update segments if provided
#         segments_data = data.get('segments', [])
#         for seg_data in segments_data:
#             position = seg_data.get('position')
#             if position is None:
#                 continue
#             WheelSegment.objects.update_or_create(
#                 wheel=wheel,
#                 position=position,
#                 defaults={
#                     'label': seg_data.get('label', ''),
#                     'multiplier': Decimal(str(seg_data.get('multiplier', '0'))),
#                     'probability_weight': int(seg_data.get('probability_weight', 0)),
#                     'color': seg_data.get('color', '#888888'),
#                     'is_active': seg_data.get('is_active', True),
#                 },
#             )

#         logger.info(
#             'Wheel %s updated by admin %s', wheel_id, request.user.telegram_id
#         )

#         return Response({'success': True, 'message': 'Wheel updated.'})


# # ─── Screen 4: Users ───────────────────────────────────────────────────────

# class AdminUserListView(APIView):
#     """
#     GET /api/v1/admin/users/

#     Query params:
#       search=<name or phone>
#       filter=all|kyc_pending|flagged
#       page=1
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request):
#         from apps.kyc.models import KYCProfile

#         # ── Overview stats ────────────────────────────────────────────
#         total_users = User.objects.count()
#         pending_kyc = KYCProfile.objects.exclude(
#             personal_info_status='verified',
#             bank_account_status='verified',
#             document_status='verified',
#         ).count()
#         # Flagged = High risk (KYC rejected)
#         flagged = KYCProfile.objects.filter(
#             Q(personal_info_status='rejected') |
#             Q(bank_account_status='rejected') |
#             Q(document_status='rejected')
#         ).count()

#         # ── User query ────────────────────────────────────────────────
#         qs = User.objects.all().order_by('-date_joined')

#         search = request.query_params.get('search', '').strip()
#         if search:
#             qs = qs.filter(
#                 Q(first_name__icontains=search) |
#                 Q(last_name__icontains=search) |
#                 Q(username__icontains=search)
#             )

#         filter_by = request.query_params.get('filter', 'all')
#         if filter_by == 'kyc_pending':
#             qs = qs.filter(
#                 kyc_profile__isnull=False,
#             ).exclude(
#                 kyc_profile__personal_info_status='verified',
#                 kyc_profile__bank_account_status='verified',
#                 kyc_profile__document_status='verified',
#             )
#         elif filter_by == 'flagged':
#             qs = qs.filter(
#                 Q(kyc_profile__personal_info_status='rejected') |
#                 Q(kyc_profile__bank_account_status='rejected') |
#                 Q(kyc_profile__document_status='rejected')
#             )

#         # ── Paginate ──────────────────────────────────────────────────
#         paginator = AdminPagination()
#         page = paginator.paginate_queryset(qs, request)

#         users_data = []
#         for user in page:
#             users_data.append({
#                 'id': user.id,
#                 'telegram_id': user.telegram_id,
#                 'name': user.get_full_name() or user.username or f'User #{user.telegram_id}',
#                 'phone_number': getattr(user, 'phone_number', '') or '',
#                 'registered_on': user.date_joined.strftime('%b %-d, %Y'),
#                 'registered_via': 'Telegram',
#                 'balance': str(_user_balance(user)),
#                 'staked': str(_user_staked(user)),
#                 'kyc_status': _kyc_status_display(user),
#                 'risk': _risk_level(user),
#             })

#         return Response({
#             'success': True,
#             'data': {
#                 'overview': {
#                     'total_users': total_users,
#                     'flagged_accounts': flagged,
#                     'pending_kyc': pending_kyc,
#                 },
#                 'users': users_data,
#                 'pagination': {
#                     'count': paginator.page.paginator.count,
#                     'next': paginator.get_next_link(),
#                     'previous': paginator.get_previous_link(),
#                 },
#             },
#         })


# class AdminUserDetailView(APIView):
#     """
#     GET /api/v1/admin/users/<user_id>/

#     User profile card + KYC status + balance.
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request, user_id):
#         try:
#             user = User.objects.get(id=user_id)
#         except User.DoesNotExist:
#             return Response(
#                 {'error': True, 'code': 'NOT_FOUND', 'message': 'User not found.'},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         # KYC details
#         kyc_data = {}
#         try:
#             kyc = user.kyc_profile
#             kyc_data = {
#                 'overall_status': kyc.overall_status,
#                 'personal_info_status': kyc.personal_info_status,
#                 'bank_account_status': kyc.bank_account_status,
#                 'document_status': kyc.document_status,
#                 'submitted_at': kyc.submitted_at,
#             }
#         except Exception:
#             kyc_data = {'overall_status': 'unverified'}

#         # Active bank account
#         bank_data = {}
#         try:
#             from apps.kyc.models import BankAccount
#             bank = BankAccount.objects.filter(user=user, is_active=True).first()
#             if bank:
#                 bank_data = {
#                     'bank_name': bank.bank_name,
#                     'account_name': bank.account_name,
#                     'account_number_masked': f'****{bank.account_number[-4:]}',
#                 }
#         except Exception:
#             pass

#         return Response({
#             'success': True,
#             'data': {
#                 'id': user.id,
#                 'telegram_id': user.telegram_id,
#                 'name': user.get_full_name() or user.username or f'User #{user.telegram_id}',
#                 'username': user.username,
#                 'registered_on': user.date_joined.strftime('%b %-d, %Y'),
#                 'registered_via': 'Telegram',
#                 'phone_number': getattr(user, 'phone_number', '') or '',
#                 'balance': str(_user_balance(user)),
#                 'staked': str(_user_staked(user)),
#                 'kyc': kyc_data,
#                 'risk': _risk_level(user),
#                 'bank_account': bank_data,
#                 'is_active': user.is_active,
#             },
#         })


# class AdminUserSpinsView(APIView):
#     """
#     GET /api/v1/admin/users/<user_id>/spins/

#     User's recent spins (paginated).
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request, user_id):
#         from apps.spin.models import Spin

#         try:
#             user = User.objects.get(id=user_id)
#         except User.DoesNotExist:
#             return Response(
#                 {'error': True, 'code': 'NOT_FOUND', 'message': 'User not found.'},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         spins = Spin.objects.filter(user=user).select_related(
#             'wheel', 'segment_landed'
#         ).order_by('-created_at')

#         paginator = AdminPagination()
#         page = paginator.paginate_queryset(spins, request)

#         spins_data = []
#         for spin in page:
#             spins_data.append({
#                 'id': str(spin.id),
#                 'wheel': spin.wheel.name if spin.wheel else '',
#                 'stake': str(spin.stake_amount),
#                 'multiplier': str(spin.segment_landed.multiplier) if spin.segment_landed else '0',
#                 'result_label': spin.segment_landed.label if spin.segment_landed else spin.outcome,
#                 'outcome': spin.outcome,
#                 'win_value': str(spin.payout_amount),
#                 'date': spin.created_at.strftime('%b %-d, %Y'),
#                 'is_welcome_spin': spin.is_welcome_spin,
#             })

#         return Response({
#             'success': True,
#             'data': {
#                 'spins': spins_data,
#                 'pagination': {
#                     'count': paginator.page.paginator.count,
#                     'next': paginator.get_next_link(),
#                     'previous': paginator.get_previous_link(),
#                 },
#             },
#         })


# class AdminUserTransactionsView(APIView):
#     """
#     GET /api/v1/admin/users/<user_id>/transactions/

#     User's wallet transaction history (paginated).
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request, user_id):
#         from apps.wallet.models import Transaction

#         try:
#             user = User.objects.get(id=user_id)
#         except User.DoesNotExist:
#             return Response(
#                 {'error': True, 'code': 'NOT_FOUND', 'message': 'User not found.'},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         txs = Transaction.objects.filter(user=user).order_by('-created_at')

#         paginator = AdminPagination()
#         page = paginator.paginate_queryset(txs, request)

#         tx_data = []
#         for tx in page:
#             # Map internal type to display label
#             label_map = {
#                 'deposit': 'Cash Deposit',
#                 'withdrawal': 'Cash Withdrawal',
#                 'win': 'Spin Win',
#                 'stake': 'Stake',
#                 'stake_release': 'Stake Return',
#                 'stake_forfeit': 'Stake Lost',
#                 'refund': 'Refund',
#             }
#             tx_data.append({
#                 'id': str(tx.id),
#                 'type': tx.type,
#                 'label': label_map.get(tx.type, tx.type.replace('_', ' ').title()),
#                 'amount': str(tx.amount),
#                 'balance_type': tx.balance_type,
#                 'balance_before': str(tx.balance_before),
#                 'balance_after': str(tx.balance_after),
#                 'status': tx.status,
#                 'reference_id': tx.reference_id,
#                 'date': tx.created_at.strftime('%b %-d, %Y'),
#             })

#         return Response({
#             'success': True,
#             'data': {
#                 'transactions': tx_data,
#                 'pagination': {
#                     'count': paginator.page.paginator.count,
#                     'next': paginator.get_next_link(),
#                     'previous': paginator.get_previous_link(),
#                 },
#             },
#         })


# # ─── Screen 5: Withdrawal Management ──────────────────────────────────────

# class AdminWithdrawalListView(APIView):
#     """
#     GET /api/v1/admin/withdrawals/

#     Query params:
#       search=<name>
#       status=pending_review|processing|completed|failed
#       page=1
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request):
#         from apps.withdrawals.models import Withdrawal

#         # ── Overview stats ────────────────────────────────────────────
#         total_pending = Withdrawal.objects.filter(
#             status__in=['pending_review', 'pending', 'processing'],
#         ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

#         total_paid = Withdrawal.objects.filter(
#             status='completed',
#         ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

#         queued_count = Withdrawal.objects.filter(
#             status='pending_review',
#         ).count()

#         # ── Query ──────────────────────────────────────────────────────
#         qs = Withdrawal.objects.select_related(
#             'user', 'bank_account'
#         ).order_by('-requested_at')

#         search = request.query_params.get('search', '').strip()
#         if search:
#             qs = qs.filter(
#                 Q(user__first_name__icontains=search) |
#                 Q(user__last_name__icontains=search) |
#                 Q(user__username__icontains=search)
#             )

#         filter_status = request.query_params.get('status', '')
#         if filter_status:
#             qs = qs.filter(status=filter_status)

#         paginator = AdminPagination()
#         page = paginator.paginate_queryset(qs, request)

#         items = []
#         for w in page:
#             # Classify amount type
#             amount = w.amount
#             if amount < 10000:
#                 amount_type = 'Small'
#             elif amount < 50000:
#                 amount_type = 'Medium'
#             else:
#                 amount_type = 'Large'

#             # Risk level based on amount type
#             risk_map = {'Small': 'Low', 'Medium': 'Medium', 'Large': 'High'}

#             items.append({
#                 'id': str(w.id),
#                 'name': w.user.get_full_name() or w.user.username or f'User #{w.user.telegram_id}',
#                 'user_id': w.user.id,
#                 'amount': str(w.amount),
#                 'bank': w.bank_account.bank_name if w.bank_account else '',
#                 'account_number_masked': (
#                     f'****{w.bank_account.account_number[-4:]}' if w.bank_account else ''
#                 ),
#                 'type': amount_type,
#                 'risk': risk_map[amount_type],
#                 'status': w.status,
#                 'requires_review': w.requires_review,
#                 'forced_manual_review': w.forced_manual_review,
#                 'reference': w.reference,
#                 'requested_at': w.requested_at.strftime('%b %-d, %Y %H:%M'),
#                 'completed_at': w.completed_at.strftime('%b %-d, %Y %H:%M') if w.completed_at else None,
#                 'failure_reason': w.failure_reason,
#             })

#         return Response({
#             'success': True,
#             'data': {
#                 'overview': {
#                     'total_pending': str(total_pending),
#                     'total_paid': str(total_paid),
#                     'queued': queued_count,
#                 },
#                 'withdrawals': items,
#                 'pagination': {
#                     'count': paginator.page.paginator.count,
#                     'next': paginator.get_next_link(),
#                     'previous': paginator.get_previous_link(),
#                 },
#             },
#         })


# class AdminWithdrawalApproveView(APIView):
#     """POST /api/v1/admin/withdrawals/<withdrawal_id>/approve/"""
#     permission_classes = [IsAdminUser]

#     def post(self, request, withdrawal_id):
#         from apps.withdrawals.models import Withdrawal
#         from apps.withdrawals.services import WithdrawalService, WithdrawalServiceError

#         try:
#             withdrawal = Withdrawal.objects.get(id=withdrawal_id)
#         except Withdrawal.DoesNotExist:
#             return Response(
#                 {'error': True, 'code': 'NOT_FOUND', 'message': 'Withdrawal not found.'},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         try:
#             notes = request.data.get('notes', '')
#             WithdrawalService.approve(withdrawal, request.user, notes=notes)
#         except WithdrawalServiceError as e:
#             return Response(
#                 {'error': True, 'code': 'CANNOT_APPROVE', 'message': str(e)},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         logger.info(
#             'Withdrawal %s approved by admin %s', withdrawal_id, request.user.id
#         )
#         return Response({'success': True, 'message': 'Withdrawal approved and processing.'})


# class AdminWithdrawalRejectView(APIView):
#     """POST /api/v1/admin/withdrawals/<withdrawal_id>/reject/"""
#     permission_classes = [IsAdminUser]

#     def post(self, request, withdrawal_id):
#         from apps.withdrawals.models import Withdrawal
#         from apps.withdrawals.services import WithdrawalService, WithdrawalServiceError

#         try:
#             withdrawal = Withdrawal.objects.get(id=withdrawal_id)
#         except Withdrawal.DoesNotExist:
#             return Response(
#                 {'error': True, 'code': 'NOT_FOUND', 'message': 'Withdrawal not found.'},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         reason = request.data.get('reason', '').strip()
#         if not reason:
#             return Response(
#                 {'error': True, 'code': 'REASON_REQUIRED',
#                  'message': 'A rejection reason is required.'},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         try:
#             WithdrawalService.reject(withdrawal, request.user, reason=reason)
#         except WithdrawalServiceError as e:
#             return Response(
#                 {'error': True, 'code': 'CANNOT_REJECT', 'message': str(e)},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         logger.info(
#             'Withdrawal %s rejected by admin %s', withdrawal_id, request.user.id
#         )
#         return Response({'success': True, 'message': 'Withdrawal rejected. Funds returned to user.'})


# # ─── Screen 6: KYC Review Queue ────────────────────────────────────────────

# class AdminKYCQueueView(APIView):
#     """
#     GET /api/v1/admin/kyc/queue/

#     Users whose KYC needs admin review.
#     Query params:
#       status=pending|requires_correction|rejected
#       search=<name>
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request):
#         from apps.kyc.models import KYCProfile, KYCDocument, BankAccount

#         qs = KYCProfile.objects.select_related('user').order_by('-submitted_at')

#         search = request.query_params.get('search', '').strip()
#         if search:
#             qs = qs.filter(
#                 Q(user__first_name__icontains=search) |
#                 Q(user__last_name__icontains=search) |
#                 Q(full_name__icontains=search)
#             )

#         filter_status = request.query_params.get('status', '')
#         if filter_status == 'pending':
#             qs = qs.filter(
#                 Q(personal_info_status='pending') |
#                 Q(bank_account_status='pending') |
#                 Q(document_status='pending')
#             )
#         elif filter_status == 'requires_correction':
#             qs = qs.filter(
#                 Q(personal_info_status='requires_correction') |
#                 Q(bank_account_status='requires_correction') |
#                 Q(document_status='requires_correction')
#             )
#         elif filter_status == 'rejected':
#             qs = qs.filter(
#                 Q(personal_info_status='rejected') |
#                 Q(bank_account_status='rejected') |
#                 Q(document_status='rejected')
#             )

#         # Stats
#         total_pending = KYCProfile.objects.filter(
#             Q(personal_info_status__in=['pending', 'requires_correction']) |
#             Q(bank_account_status__in=['pending', 'requires_correction']) |
#             Q(document_status__in=['pending', 'requires_correction'])
#         ).count()
#         total_approved = KYCProfile.objects.filter(
#             personal_info_status='verified',
#             bank_account_status='verified',
#             document_status='verified',
#         ).count()
#         total_rejected = KYCProfile.objects.filter(
#             Q(personal_info_status='rejected') |
#             Q(bank_account_status='rejected') |
#             Q(document_status='rejected')
#         ).count()

#         paginator = AdminPagination()
#         page = paginator.paginate_queryset(qs, request)

#         items = []
#         for kyc in page:
#             # Get document
#             doc = KYCDocument.objects.filter(
#                 user=kyc.user, is_active=True
#             ).order_by('-uploaded_at').first()

#             # Get bank account
#             try:
#                 bank = BankAccount.objects.filter(user=kyc.user, is_active=True).first()
#                 bank_data = {
#                     'bank_name': bank.bank_name,
#                     'account_name': bank.account_name,
#                     'account_number_masked': f'****{bank.account_number[-4:]}',
#                 } if bank else {}
#             except Exception:
#                 bank_data = {}

#             items.append({
#                 'id': str(kyc.id),
#                 'user_id': kyc.user.id,
#                 'name': kyc.full_name or kyc.user.get_full_name() or '',
#                 'telegram_id': kyc.user.telegram_id,
#                 'overall_status': kyc.overall_status,
#                 'personal_info': {
#                     'status': kyc.personal_info_status,
#                     'reason': kyc.personal_info_reason,
#                 },
#                 'bank_account': {
#                     'status': kyc.bank_account_status,
#                     'reason': kyc.bank_account_reason,
#                     **bank_data,
#                 },
#                 'document': {
#                     'status': kyc.document_status,
#                     'reason': kyc.document_reason,
#                     'filename': doc.original_filename if doc else None,
#                     'type': doc.document_type if doc else None,
#                     'file_url': doc.file.url if doc and doc.file else None,
#                 },
#                 'submitted_at': kyc.submitted_at.strftime('%b %-d, %Y') if kyc.submitted_at else None,
#                 'last_resubmission_at': (
#                     kyc.last_resubmission_at.strftime('%b %-d, %Y')
#                     if kyc.last_resubmission_at else None
#                 ),
#             })

#         return Response({
#             'success': True,
#             'data': {
#                 'overview': {
#                     'total_pending': total_pending,
#                     'total_approved': total_approved,
#                     'total_rejected': total_rejected,
#                 },
#                 'queue': items,
#                 'pagination': {
#                     'count': paginator.page.paginator.count,
#                     'next': paginator.get_next_link(),
#                     'previous': paginator.get_previous_link(),
#                 },
#             },
#         })


# class AdminKYCApproveView(APIView):
#     """
#     POST /api/v1/admin/kyc/<kyc_id>/approve/

#     Manually approve a specific KYC section.
#     Body: { "section": "personal_info" | "bank_account" | "document" | "all" }
#     """
#     permission_classes = [IsAdminUser]

#     def post(self, request, kyc_id):
#         from apps.kyc.models import KYCProfile

#         try:
#             kyc = KYCProfile.objects.get(id=kyc_id)
#         except KYCProfile.DoesNotExist:
#             return Response(
#                 {'error': True, 'code': 'NOT_FOUND', 'message': 'KYC profile not found.'},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         section = request.data.get('section', 'all')
#         valid_sections = ['personal_info', 'bank_account', 'document', 'all']

#         if section not in valid_sections:
#             return Response(
#                 {'error': True, 'code': 'INVALID_SECTION',
#                  'message': f'Section must be one of: {valid_sections}'},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         from apps.kyc.models import KYCProfile as KP
#         if section == 'all' or section == 'personal_info':
#             kyc.personal_info_status = KP.SectionStatus.VERIFIED
#             kyc.personal_info_reason = ''
#         if section == 'all' or section == 'bank_account':
#             kyc.bank_account_status = KP.SectionStatus.VERIFIED
#             kyc.bank_account_reason = ''
#         if section == 'all' or section == 'document':
#             kyc.document_status = KP.SectionStatus.VERIFIED
#             kyc.document_reason = ''

#         kyc.reviewed_at = timezone.now()
#         kyc.save()

#         logger.info(
#             'KYC %s section=%s approved by admin %s', kyc_id, section, request.user.id
#         )

#         return Response({
#             'success': True,
#             'message': f'KYC {section} section approved.',
#             'data': {'overall_status': kyc.overall_status},
#         })


# class AdminKYCRejectView(APIView):
#     """
#     POST /api/v1/admin/kyc/<kyc_id>/reject/

#     Manually reject a KYC section with a reason.
#     Body: {
#       "section": "personal_info" | "bank_account" | "document",
#       "reason": "Document appears fake"
#     }
#     """
#     permission_classes = [IsAdminUser]

#     def post(self, request, kyc_id):
#         from apps.kyc.models import KYCProfile

#         try:
#             kyc = KYCProfile.objects.get(id=kyc_id)
#         except KYCProfile.DoesNotExist:
#             return Response(
#                 {'error': True, 'code': 'NOT_FOUND', 'message': 'KYC profile not found.'},
#                 status=status.HTTP_404_NOT_FOUND,
#             )

#         section = request.data.get('section', '')
#         reason = request.data.get('reason', '').strip()

#         if section not in ['personal_info', 'bank_account', 'document']:
#             return Response(
#                 {'error': True, 'code': 'INVALID_SECTION',
#                  'message': 'Section must be personal_info, bank_account, or document'},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         if not reason:
#             return Response(
#                 {'error': True, 'code': 'REASON_REQUIRED',
#                  'message': 'A rejection reason is required.'},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         from apps.kyc.models import KYCProfile as KP
#         if section == 'personal_info':
#             kyc.personal_info_status = KP.SectionStatus.REJECTED
#             kyc.personal_info_reason = reason
#         elif section == 'bank_account':
#             kyc.bank_account_status = KP.SectionStatus.REJECTED
#             kyc.bank_account_reason = reason
#         elif section == 'document':
#             kyc.document_status = KP.SectionStatus.REJECTED
#             kyc.document_reason = reason

#         kyc.reviewed_at = timezone.now()
#         kyc.save()

#         logger.info(
#             'KYC %s section=%s rejected by admin %s', kyc_id, section, request.user.id
#         )

#         return Response({
#             'success': True,
#             'message': f'KYC {section} section rejected.',
#             'data': {'overall_status': kyc.overall_status},
#         })


# # ─── Screen 7: Fraud & Risk Monitor ───────────────────────────────────────

# class AdminFraudMonitorView(APIView):
#     """
#     GET /api/v1/admin/fraud/

#     Users with risk indicators:
#     - Multiple accounts (same phone)
#     - High withdrawal frequency
#     - Large single wins
#     - KYC rejected
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request):
#         from apps.spin.models import Spin
#         from apps.withdrawals.models import Withdrawal

#         thirty_days_ago = timezone.now() - timedelta(days=30)

#         # High-frequency withdrawers (>2 in 7 days)
#         seven_days_ago = timezone.now() - timedelta(days=7)
#         high_freq_withdrawers = Withdrawal.objects.filter(
#             requested_at__gte=seven_days_ago,
#         ).values('user').annotate(
#             count=Count('id')
#         ).filter(count__gt=2).values_list('user', flat=True)

#         # Large wins (>5× stake)
#         large_wins = Spin.objects.filter(
#             outcome='win',
#             created_at__gte=thirty_days_ago,
#             payout_amount__gt=50000,
#         ).select_related('user').order_by('-payout_amount')[:20]

#         # KYC rejected users
#         from apps.kyc.models import KYCProfile
#         rejected_kyc = KYCProfile.objects.filter(
#             Q(personal_info_status='rejected') |
#             Q(bank_account_status='rejected') |
#             Q(document_status='rejected')
#         ).select_related('user')

#         flagged_users = []
#         seen_ids = set()

#         for uid in high_freq_withdrawers:
#             if uid in seen_ids:
#                 continue
#             seen_ids.add(uid)
#             try:
#                 u = User.objects.get(id=uid)
#                 flagged_users.append({
#                     'user_id': u.id,
#                     'name': u.get_full_name() or f'User #{u.telegram_id}',
#                     'flag': 'High withdrawal frequency',
#                     'risk': 'High',
#                 })
#             except User.DoesNotExist:
#                 pass

#         for spin in large_wins:
#             if spin.user_id in seen_ids:
#                 continue
#             seen_ids.add(spin.user_id)
#             flagged_users.append({
#                 'user_id': spin.user.id,
#                 'name': spin.user.get_full_name() or f'User #{spin.user.telegram_id}',
#                 'flag': f'Large win ₦{spin.payout_amount}',
#                 'risk': 'Medium',
#             })

#         for kyc in rejected_kyc[:20]:
#             if kyc.user_id in seen_ids:
#                 continue
#             seen_ids.add(kyc.user_id)
#             flagged_users.append({
#                 'user_id': kyc.user.id,
#                 'name': kyc.full_name or kyc.user.get_full_name() or '',
#                 'flag': 'KYC rejected',
#                 'risk': 'High',
#             })

#         return Response({
#             'success': True,
#             'data': {
#                 'flagged_users': flagged_users,
#                 'total_flagged': len(flagged_users),
#             },
#         })


# # ─── Screen 8: Audit Logs ─────────────────────────────────────────────────

# class AdminAuditLogsView(APIView):
#     """
#     GET /api/v1/admin/audit-logs/

#     Recent admin actions (withdrawals, KYC decisions, RTP changes).
#     Assembled from existing model timestamps + reviewed_by fields.
#     """
#     permission_classes = [IsAdminUser]

#     def get(self, request):
#         from apps.withdrawals.models import Withdrawal
#         from apps.kyc.models import KYCProfile

#         logs = []

#         # Withdrawal approve/reject actions
#         reviewed_withdrawals = Withdrawal.objects.filter(
#             reviewed_at__isnull=False
#         ).select_related('user', 'reviewed_by').order_by('-reviewed_at')[:50]

#         for w in reviewed_withdrawals:
#             action = 'approved' if w.status in ['completed', 'processing', 'pending'] else 'rejected'
#             logs.append({
#                 'type': 'withdrawal',
#                 'action': f'Withdrawal {action}',
#                 'amount': str(w.amount),
#                 'target_user': w.user.get_full_name() or f'User #{w.user.telegram_id}',
#                 'performed_by': (
#                     w.reviewed_by.get_full_name() if w.reviewed_by else 'System'
#                 ),
#                 'timestamp': w.reviewed_at.isoformat() if w.reviewed_at else None,
#                 'notes': w.review_notes,
#             })

#         # KYC review actions
#         reviewed_kyc = KYCProfile.objects.filter(
#             reviewed_at__isnull=False
#         ).select_related('user').order_by('-reviewed_at')[:50]

#         for kyc in reviewed_kyc:
#             logs.append({
#                 'type': 'kyc',
#                 'action': f'KYC review — {kyc.overall_status}',
#                 'target_user': kyc.full_name or kyc.user.get_full_name() or '',
#                 'performed_by': 'Admin',
#                 'timestamp': kyc.reviewed_at.isoformat() if kyc.reviewed_at else None,
#                 'notes': '',
#             })

#         # Sort all logs by timestamp desc
#         logs.sort(key=lambda x: x['timestamp'] or '', reverse=True)
#         logs = logs[:100]

#         paginator = AdminPagination()
#         page = paginator.paginate_queryset(logs, request)

#         return Response({
#             'success': True,
#             'data': {
#                 'logs': page,
#                 'pagination': {
#                     'count': len(logs),
#                     'next': paginator.get_next_link(),
#                     'previous': paginator.get_previous_link(),
#                 },
#             },
#         })



# class AdminDevLoginView(APIView):
#     """
#     DEV ONLY
#     POST /api/v1/auth/admin-dev-login/
#     """
#     permission_classes = [AllowAny]

#     def post(self, request):
#         telegram_id = request.data.get("telegram_id")

#         if not telegram_id:
#             return Response(
#                 {"error": "telegram_id required"},
#                 status=status.HTTP_400_BAD_REQUEST
#             )

#         try:
#             user = User.objects.get(telegram_id=telegram_id)
#         except User.DoesNotExist:
#             return Response(
#                 {"error": "User not found"},
#                 status=status.HTTP_404_NOT_FOUND
#             )

#         if not (user.is_staff or user.is_superuser):
#             return Response(
#                 {"error": "Not an admin"},
#                 status=status.HTTP_403_FORBIDDEN
#             )

#         refresh = RefreshToken.for_user(user)

#         return Response({
#             "success": True,
#             "data": {
#                 "access_token": str(refresh.access_token),
#                 "refresh_token": str(refresh),
#                 "token_type": "Bearer",
#             }
#         })