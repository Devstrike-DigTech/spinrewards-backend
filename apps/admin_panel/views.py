import logging
from decimal import Decimal
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import timedelta

from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework import status

from common.permissions import IsAdminUser
from apps.spin.models import RTPTier, RTPOutcome, SpinResult
from apps.users.models import User
from apps.wallet.models import Transaction
from apps.kyc.models import KYC
from apps.kyc.services import KYCService
from apps.withdrawals.models import WithdrawalRequest
from apps.withdrawals.services import WithdrawalService
from .models import AdminAuditLog
from .serializers import (
    RTPTierAdminSerializer,
    RTPTierCreateSerializer,
    AdminUserSerializer,
    AdminKYCSerializer,
    AdminWithdrawalSerializer,
    AdminAuditLogSerializer,
)

logger = logging.getLogger(__name__)


# ── RTP Tier Views ─────────────────────────────────────────────────────────────

class AdminRTPTierListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        tiers = RTPTier.objects.prefetch_related('outcomes').all()
        return Response({
            'success': True,
            'data': RTPTierAdminSerializer(tiers, many=True).data,
        })

    def post(self, request):
        serializer = RTPTierCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tier = serializer.save(created_by=request.user)

        AdminAuditLog.objects.create(
            admin=request.user,
            action='rtp_tier_created',
            target_model='RTPTier',
            target_id=str(tier.id),
            new_state=RTPTierAdminSerializer(tier).data,
            ip_address=request.META.get('REMOTE_ADDR'),
        )

        return Response(
            {'success': True, 'data': RTPTierAdminSerializer(tier).data},
            status=status.HTTP_201_CREATED,
        )


class AdminRTPTierDetailView(APIView):
    permission_classes = [IsAdminUser]

    def _get_tier(self, pk):
        try:
            return RTPTier.objects.prefetch_related('outcomes').get(pk=pk)
        except RTPTier.DoesNotExist:
            return None

    def patch(self, request, pk):
        tier = self._get_tier(pk)
        if not tier:
            return Response({'error': True, 'code': 'NOT_FOUND', 'message': 'Tier not found.'}, status=404)

        previous_state = RTPTierAdminSerializer(tier).data

        serializer = RTPTierCreateSerializer(tier, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        tier = serializer.save()

        AdminAuditLog.objects.create(
            admin=request.user,
            action='rtp_tier_updated',
            target_model='RTPTier',
            target_id=str(tier.id),
            previous_state=previous_state,
            new_state=RTPTierAdminSerializer(tier).data,
            ip_address=request.META.get('REMOTE_ADDR'),
        )

        return Response({'success': True, 'data': RTPTierAdminSerializer(tier).data})

    def delete(self, request, pk):
        tier = self._get_tier(pk)
        if not tier:
            return Response({'error': True, 'code': 'NOT_FOUND', 'message': 'Tier not found.'}, status=404)

        tier.is_active = False
        tier.save(update_fields=['is_active', 'updated_at'])

        AdminAuditLog.objects.create(
            admin=request.user,
            action='rtp_tier_deactivated',
            target_model='RTPTier',
            target_id=str(tier.id),
            ip_address=request.META.get('REMOTE_ADDR'),
        )

        return Response({'success': True, 'data': {'id': str(tier.id), 'is_active': False}})


# ── User Management Views ──────────────────────────────────────────────────────

class AdminUserListView(ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = AdminUserSerializer

    def get_queryset(self):
        qs = User.objects.select_related('kyc').all()
        search = self.request.query_params.get('search')
        kyc_status = self.request.query_params.get('kyc_status')
        is_active = self.request.query_params.get('is_active')

        if search:
            qs = qs.filter(
                Q(telegram_id__icontains=search) | Q(username__icontains=search)
            )
        if kyc_status:
            qs = qs.filter(kyc__status=kyc_status)
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == 'true')

        return qs

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})


class AdminUserDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, pk):
        try:
            user = User.objects.select_related('kyc', 'wallet').get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': True, 'code': 'NOT_FOUND', 'message': 'User not found.'}, status=404)

        from apps.wallet.services import WalletService
        data = AdminUserSerializer(user).data
        data['coin_balance'] = str(WalletService.get_balance(user, 'coin'))
        data['cash_balance'] = str(WalletService.get_balance(user, 'cash'))
        data['total_spins'] = SpinResult.objects.filter(user=user).count()

        return Response({'success': True, 'data': data})

    def patch(self, request, pk):
        try:
            user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': True, 'code': 'NOT_FOUND', 'message': 'User not found.'}, status=404)

        is_active = request.data.get('is_active')
        if is_active is not None:
            user.is_active = is_active
            user.save(update_fields=['is_active', 'updated_at'])

        AdminAuditLog.objects.create(
            admin=request.user,
            action='user_updated',
            target_model='User',
            target_id=str(user.id),
            new_state={'is_active': user.is_active},
            ip_address=request.META.get('REMOTE_ADDR'),
        )

        return Response({'success': True, 'data': AdminUserSerializer(user).data})


# ── KYC Management Views ───────────────────────────────────────────────────────

class AdminKYCListView(ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = AdminKYCSerializer

    def get_queryset(self):
        qs = KYC.objects.select_related('user').all()
        status_filter = self.request.query_params.get('status', 'pending')
        return qs.filter(status=status_filter)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})


class AdminKYCApproveView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        try:
            kyc = KYC.objects.get(pk=pk)
        except KYC.DoesNotExist:
            return Response({'error': True, 'code': 'NOT_FOUND', 'message': 'KYC not found.'}, status=404)

        kyc = KYCService.approve(kyc, admin_user=request.user)
        return Response({'success': True, 'data': {'id': str(kyc.id), 'status': kyc.status}})


class AdminKYCRejectView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        try:
            kyc = KYC.objects.get(pk=pk)
        except KYC.DoesNotExist:
            return Response({'error': True, 'code': 'NOT_FOUND', 'message': 'KYC not found.'}, status=404)

        reason = request.data.get('reason', '')
        if not reason:
            return Response(
                {'error': True, 'code': 'VALIDATION_ERROR', 'message': 'Rejection reason is required.'},
                status=422,
            )

        kyc = KYCService.reject(kyc, admin_user=request.user, reason=reason)
        return Response({'success': True, 'data': {'id': str(kyc.id), 'status': kyc.status}})


# ── Withdrawal Management Views ────────────────────────────────────────────────

class AdminWithdrawalListView(ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = AdminWithdrawalSerializer

    def get_queryset(self):
        qs = WithdrawalRequest.objects.select_related('user').all()
        status_filter = self.request.query_params.get('status', 'pending')
        return qs.filter(status=status_filter)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})


class AdminWithdrawalApproveView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        try:
            withdrawal = WithdrawalRequest.objects.get(pk=pk)
        except WithdrawalRequest.DoesNotExist:
            return Response({'error': True, 'code': 'NOT_FOUND', 'message': 'Withdrawal not found.'}, status=404)

        withdrawal.status = WithdrawalRequest.Status.PROCESSING
        withdrawal.save(update_fields=['status', 'updated_at'])

        from apps.withdrawals.tasks import process_withdrawal_payout
        process_withdrawal_payout.delay(str(withdrawal.id))

        return Response({'success': True, 'data': {'id': str(withdrawal.id), 'status': withdrawal.status}})


class AdminWithdrawalRejectView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        try:
            withdrawal = WithdrawalRequest.objects.get(pk=pk)
        except WithdrawalRequest.DoesNotExist:
            return Response({'error': True, 'code': 'NOT_FOUND', 'message': 'Withdrawal not found.'}, status=404)

        reason = request.data.get('reason', 'Rejected by admin')
        WithdrawalService.reverse(withdrawal, reason=reason)
        return Response({'success': True, 'data': {'id': str(withdrawal.id), 'status': 'reversed'}})


# ── Analytics View ─────────────────────────────────────────────────────────────

class AdminAnalyticsView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        from_date = request.query_params.get('from', (timezone.now() - timedelta(days=30)).date())
        to_date = request.query_params.get('to', timezone.now().date())

        users_total = User.objects.count()
        users_new = User.objects.filter(created_at__date__gte=from_date, created_at__date__lte=to_date).count()

        spins = SpinResult.objects.filter(created_at__date__gte=from_date, created_at__date__lte=to_date)
        total_staked = spins.aggregate(total=Sum('stake'))['total'] or Decimal('0')
        total_won = spins.aggregate(total=Sum('win_amount'))['total'] or Decimal('0')
        effective_rtp = (total_won / total_staked * 100) if total_staked else Decimal('0')

        deposits = Transaction.objects.filter(
            type=Transaction.Type.DEPOSIT,
            status=Transaction.Status.COMPLETED,
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        )
        withdrawals = WithdrawalRequest.objects.filter(
            created_at__date__gte=from_date,
            created_at__date__lte=to_date,
        )

        return Response({
            'success': True,
            'data': {
                'period': {'from': str(from_date), 'to': str(to_date)},
                'users': {
                    'total': users_total,
                    'new_this_period': users_new,
                },
                'spins': {
                    'total': spins.count(),
                    'total_staked': str(total_staked),
                    'total_won': str(total_won),
                    'effective_rtp': f'{effective_rtp:.2f}',
                },
                'deposits': {
                    'total_count': deposits.count(),
                    'total_amount': str(deposits.aggregate(total=Sum('amount'))['total'] or 0),
                },
                'withdrawals': {
                    'total_count': withdrawals.count(),
                    'total_amount': str(withdrawals.filter(status='completed').aggregate(total=Sum('amount'))['total'] or 0),
                    'pending_count': withdrawals.filter(status='pending').count(),
                },
            }
        })


# ── Audit Log View ─────────────────────────────────────────────────────────────

class AdminAuditLogListView(ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = AdminAuditLogSerializer

    def get_queryset(self):
        qs = AdminAuditLog.objects.select_related('admin').all()
        action = self.request.query_params.get('action')
        admin_id = self.request.query_params.get('admin_id')
        if action:
            qs = qs.filter(action=action)
        if admin_id:
            qs = qs.filter(admin__id=admin_id)
        return qs

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})
