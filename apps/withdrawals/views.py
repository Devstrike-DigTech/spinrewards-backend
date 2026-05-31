# from rest_framework.views import APIView
# from rest_framework.generics import ListAPIView, RetrieveAPIView
# from rest_framework.permissions import IsAuthenticated
# from rest_framework.response import Response
# from rest_framework import status

# from apps.payments.services import PaymentService
# from .services import WithdrawalService
# from .models import WithdrawalRequest
# from .serializers import WithdrawalRequestSerializer, WithdrawalResponseSerializer


# class WithdrawalView(APIView):
#     """POST /api/v1/withdrawals/ — Request a withdrawal."""
#     permission_classes = [IsAuthenticated]

#     def post(self, request):
#         serializer = WithdrawalRequestSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)

#         data = serializer.validated_data

#         # Resolve bank account name before proceeding
#         account_name = PaymentService.resolve_bank_account(
#             data['bank_account'], data['bank_code']
#         )

#         withdrawal = WithdrawalService.request(
#             user=request.user,
#             amount=data['amount'],
#             bank_account=data['bank_account'],
#             bank_code=data['bank_code'],
#             account_name=account_name,
#         )

#         return Response(
#             {'success': True, 'data': WithdrawalResponseSerializer(withdrawal).data},
#             status=status.HTTP_201_CREATED,
#         )


# class WithdrawalListView(ListAPIView):
#     """GET /api/v1/withdrawals/ — Get withdrawal history."""
#     permission_classes = [IsAuthenticated]
#     serializer_class = WithdrawalResponseSerializer

#     def get_queryset(self):
#         return WithdrawalRequest.objects.filter(user=self.request.user)

#     def list(self, request, *args, **kwargs):
#         response = super().list(request, *args, **kwargs)
#         return Response({'success': True, 'data': response.data})


# class WithdrawalDetailView(RetrieveAPIView):
#     """GET /api/v1/withdrawals/{id}/ — Get withdrawal detail."""
#     permission_classes = [IsAuthenticated]
#     serializer_class = WithdrawalResponseSerializer

#     def get_queryset(self):
#         return WithdrawalRequest.objects.filter(user=self.request.user)

#     def retrieve(self, request, *args, **kwargs):
#         response = super().retrieve(request, *args, **kwargs)
#         return Response({'success': True, 'data': response.data})

"""
Withdrawal HTTP endpoints.

  POST /api/v1/withdrawals/                         Tiered flow (auto under threshold)
  POST /api/v1/withdrawals/manual-review/           Forced manual review (admin always)
  GET  /api/v1/withdrawals/list/                    User's withdrawal history
  GET  /api/v1/withdrawals/<uuid>/                  Specific withdrawal
  POST /api/v1/withdrawals/<uuid>/cancel/           Cancel pending_review
  GET  /api/v1/withdrawals/limits/                  Show limits & remaining today
"""
import logging
from decimal import Decimal, InvalidOperation

from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.kyc.models import BankAccount
from apps.wallet.services import WalletService
from apps.withdrawals.bank_account_service import BankAccountError, BankAccountService

from .models import Withdrawal
from .serializers import (
    WithdrawalRequestSerializer,
    WithdrawalResponseSerializer,
)
from .services import (
    AUTO_PAYOUT_THRESHOLD,
    MAX_DAILY_COUNT,
    MAX_DAILY_WITHDRAWAL,
    MAX_WITHDRAWAL_PER_TXN,
    MIN_WITHDRAWAL,
    WithdrawalService,
    WithdrawalServiceError,
)

logger = logging.getLogger(__name__)


class WithdrawalRequestView(APIView):
    """
    POST /api/v1/withdrawals/

    Body (one of two flows):
      Flow A — saved account:
        { "amount": 5000, "saved_account_id": "<uuid>" }

      Flow B — new account inline (resolve + name-match + save):
        { "amount": 5000, "bank_code": "058", "account_number": "0123456789" }

    Tiered processing:
      - Amount < AUTO_PAYOUT_THRESHOLD → auto-process
      - Amount ≥ AUTO_PAYOUT_THRESHOLD → manual review queue
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        saved_account_id = request.data.get('saved_account_id')
        bank_code = (request.data.get('bank_code') or '').strip()
        account_number = (request.data.get('account_number') or '').strip()

        # ── Validate amount ──────────────────────────────────────────────
        raw_amount = request.data.get('amount')
        if raw_amount is None:
            return Response(
                {'error': True, 'code': 'MISSING_AMOUNT',
                 'message': 'Amount is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError):
            return Response(
                {'error': True, 'code': 'INVALID_AMOUNT',
                 'message': 'Amount must be a number.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if amount <= 0:
            return Response(
                {'error': True, 'code': 'INVALID_AMOUNT',
                 'message': 'Amount must be greater than zero.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Resolve which account to use ─────────────────────────────────
        bank_account = None

        if saved_account_id:
            # Flow A: existing saved account
            try:
                bank_account = BankAccount.objects.get(
                    id=saved_account_id, user=user, is_active=True,
                )
            except BankAccount.DoesNotExist:
                return Response(
                    {'error': True, 'code': 'ACCOUNT_NOT_FOUND',
                     'message': 'Saved bank account not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

        elif bank_code and account_number:
            # Flow B: new account — resolve + match + save
            try:
                resolved = BankAccountService.resolve_and_match(
                    user=user, bank_code=bank_code, account_number=account_number,
                )
                bank_account = BankAccountService.save_account(user, resolved)
            except BankAccountError as e:
                payload = {'error': True, 'code': e.code, 'message': e.message}
                if e.extra:
                    payload.update(e.extra)
                http_status = {
                    'KYC_REQUIRED':   status.HTTP_403_FORBIDDEN,
                    'PROVIDER_ERROR': status.HTTP_503_SERVICE_UNAVAILABLE,
                }.get(e.code, status.HTTP_400_BAD_REQUEST)
                return Response(payload, status=http_status)

        else:
            return Response(
                {
                    'error': True,
                    'code': 'MISSING_ACCOUNT',
                    'message': (
                        'Provide either a saved_account_id, or both bank_code '
                        'and account_number for a new account.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Create the withdrawal request ────────────────────────────────
        try:
            withdrawal = WithdrawalService.request(
                user=user,
                amount=amount,
                bank_account=bank_account,    # ← pass the FK
            )
        except WithdrawalServiceError as e:
            return Response(
                {'error': True, 'code': 'WITHDRAWAL_REJECTED', 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {'success': True, 'data': WithdrawalResponseSerializer(withdrawal).data},
            status=status.HTTP_201_CREATED,
        )

class WithdrawalRequestManualReviewView(APIView):
    """
    POST /api/v1/withdrawals/manual-review/

    Forced manual review flow:
      - ALL withdrawals queued for admin approval regardless of amount.
      - Status always starts as pending_review.
      - User can still cancel before admin acts.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WithdrawalRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            withdrawal = WithdrawalService.request_with_manual_review(
                user=request.user,
                amount=serializer.validated_data['amount'],
            )
        except WithdrawalServiceError as e:
            return Response(
                {'error': True, 'code': 'WITHDRAWAL_REJECTED', 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {'success': True, 'data': WithdrawalResponseSerializer(withdrawal).data},
            status=status.HTTP_201_CREATED,
        )


class WithdrawalListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = WithdrawalResponseSerializer

    def get_queryset(self):
        return Withdrawal.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})


class WithdrawalDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, withdrawal_id):
        try:
            withdrawal = Withdrawal.objects.get(
                id=withdrawal_id, user=request.user,
            )
        except Withdrawal.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Withdrawal not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {'success': True, 'data': WithdrawalResponseSerializer(withdrawal).data},
            status=status.HTTP_200_OK,
        )


class WithdrawalCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, withdrawal_id):
        try:
            withdrawal = Withdrawal.objects.get(
                id=withdrawal_id, user=request.user,
            )
        except Withdrawal.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Withdrawal not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            withdrawal = WithdrawalService.cancel(request.user, withdrawal)
        except WithdrawalServiceError as e:
            return Response(
                {'error': True, 'code': 'CANNOT_CANCEL', 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {'success': True, 'data': WithdrawalResponseSerializer(withdrawal).data},
            status=status.HTTP_200_OK,
        )


class WithdrawalLimitsView(APIView):
    """GET /api/v1/withdrawals/limits/"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_withdrawals = Withdrawal.objects.filter(
            user=user, requested_at__gte=today_start,
        ).exclude(status__in=['cancelled', 'rejected'])

        count_today = today_withdrawals.count()
        total_today = sum(
            (w.amount for w in today_withdrawals), start=Decimal('0'),
        )

        max_daily = MAX_DAILY_WITHDRAWAL()
        max_count = MAX_DAILY_COUNT()
        cash_balance = WalletService.get_balance(user, 'cash')

        try:
            kyc_approved = user.kyc_profile.can_withdraw
        except Exception:
            kyc_approved = False

        return Response(
            {
                'success': True,
                'data': {
                    'min_withdrawal': str(MIN_WITHDRAWAL()),
                    'max_per_transaction': str(MAX_WITHDRAWAL_PER_TXN()),
                    'max_daily_amount': str(max_daily),
                    'max_daily_count': max_count,
                    'auto_payout_threshold': str(AUTO_PAYOUT_THRESHOLD()),
                    'cash_balance': str(cash_balance),
                    'today_total': str(total_today),
                    'today_count': count_today,
                    'remaining_today_amount': str(max(Decimal('0'), max_daily - total_today)),
                    'remaining_today_count': max(0, max_count - count_today),
                    'kyc_approved': kyc_approved,
                },
            },
            status=status.HTTP_200_OK,
        )