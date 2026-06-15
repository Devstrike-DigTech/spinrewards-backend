
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
from apps.withdrawals.crypto_wallet_service import CryptoWalletError, CryptoWalletService

from .models import CryptoWallet, Withdrawal
from .serializers import (
    CryptoWalletSerializer,
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
    
        serializer = WithdrawalRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
    
        rail = data['rail']
        amount = data['amount']
    
        # ─── BANK RAIL ───────────────────────────────────────────────────
        if rail == 'bank':
            bank_account = None
    
            if data.get('saved_account_id'):
                # Look up the saved account
                from apps.kyc.models import BankAccount
                try:
                    bank_account = BankAccount.objects.get(
                        id=data['saved_account_id'],
                        user=user,
                        is_active=True,
                    )
                except BankAccount.DoesNotExist:
                    return Response(
                        {'error': True, 'code': 'BANK_ACCOUNT_NOT_FOUND',
                        'message': 'Saved bank account not found or inactive.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )
    
            elif data.get('bank_code') and data.get('account_number'):
                # Resolve + create new account
                from apps.withdrawals.bank_account_service import BankAccountService
                try:
                    bank_account = BankAccountService.resolve_and_save(
                        user=user,
                        bank_code=data['bank_code'],
                        account_number=data['account_number'],
                    )
                except Exception as e:
                    return Response(
                        {'error': True, 'code': 'BANK_RESOLVE_FAILED',
                        'message': str(e)},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
    
            # Submit
            try:
                withdrawal = WithdrawalService.request(
                    user=user,
                    amount=amount,
                    rail='bank',
                    bank_account=bank_account,
                )
            except WithdrawalServiceError as e:
                return Response(
                    {'error': True,
                    'code': getattr(e, 'code', 'WITHDRAWAL_REJECTED'),
                    'message': str(e)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
    
        # ─── CRYPTO RAIL ─────────────────────────────────────────────────
        elif rail == 'crypto':
            from apps.withdrawals.crypto_wallet_service import (
                CryptoWalletService, CryptoWalletError,
            )
    
            wallet_address = ''
            network = data.get('network') or 'TRC20'
    
            if data.get('saved_wallet_id'):
                # Look up saved wallet
                try:
                    wallet = CryptoWallet.objects.get(
                        id=data['saved_wallet_id'],
                        user=user,
                        is_active=True,
                    )
                    wallet_address = wallet.address
                    network = wallet.network
                except CryptoWallet.DoesNotExist:
                    return Response(
                        {'error': True, 'code': 'WALLET_NOT_FOUND',
                        'message': 'Saved crypto wallet not found or inactive.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )
    
            elif data.get('wallet_address'):
                wallet_address = data['wallet_address']
                # Optionally save it (mirroring bank inline-create flow)
                try:
                    CryptoWalletService.save(
                        user=user,
                        address=wallet_address,
                        network=network,
                    )
                except CryptoWalletError as e:
                    return Response(
                        {'error': True,
                        'code': getattr(e, 'code', 'INVALID_WALLET_ADDRESS'),
                        'message': str(e)},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
    
            # Submit
            try:
                withdrawal = WithdrawalService.request(
                    user=user,
                    amount=amount,
                    rail='crypto',
                    wallet_address=wallet_address,
                    network=network,
                )
            except WithdrawalServiceError as e:
                return Response(
                    {'error': True,
                    'code': getattr(e, 'code', 'WITHDRAWAL_REJECTED'),
                    'message': str(e)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
    
        else:
            return Response(
                {'error': True, 'code': 'UNKNOWN_RAIL',
                'message': f'Unknown rail: {rail}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    
        return Response(
            {'success': True, 'data': WithdrawalResponseSerializer(withdrawal).data},
            status=status.HTTP_201_CREATED,
        )

    # def post(self, request):
    #     user = request.user
    #     saved_account_id = request.data.get('saved_account_id')
    #     bank_code = (request.data.get('bank_code') or '').strip()
    #     account_number = (request.data.get('account_number') or '').strip()

    #     # ── Validate amount ──────────────────────────────────────────────
    #     raw_amount = request.data.get('amount')
    #     if raw_amount is None:
    #         return Response(
    #             {'error': True, 'code': 'MISSING_AMOUNT',
    #              'message': 'Amount is required.'},
    #             status=status.HTTP_400_BAD_REQUEST,
    #         )
    #     try:
    #         amount = Decimal(str(raw_amount))
    #     except (InvalidOperation, TypeError):
    #         return Response(
    #             {'error': True, 'code': 'INVALID_AMOUNT',
    #              'message': 'Amount must be a number.'},
    #             status=status.HTTP_400_BAD_REQUEST,
    #         )
    #     if amount <= 0:
    #         return Response(
    #             {'error': True, 'code': 'INVALID_AMOUNT',
    #              'message': 'Amount must be greater than zero.'},
    #             status=status.HTTP_400_BAD_REQUEST,
    #         )

    #     # ── Resolve which account to use ─────────────────────────────────
    #     bank_account = None

    #     if saved_account_id:
    #         # Flow A: existing saved account
    #         try:
    #             bank_account = BankAccount.objects.get(
    #                 id=saved_account_id, user=user, is_active=True,
    #             )
    #         except BankAccount.DoesNotExist:
    #             return Response(
    #                 {'error': True, 'code': 'ACCOUNT_NOT_FOUND',
    #                  'message': 'Saved bank account not found.'},
    #                 status=status.HTTP_404_NOT_FOUND,
    #             )

    #     elif bank_code and account_number:
    #         # Flow B: new account — resolve + match + save
    #         try:
    #             resolved = BankAccountService.resolve_and_match(
    #                 user=user, bank_code=bank_code, account_number=account_number,
    #             )
    #             bank_account = BankAccountService.save_account(user, resolved)
    #         except BankAccountError as e:
    #             payload = {'error': True, 'code': e.code, 'message': e.message}
    #             if e.extra:
    #                 payload.update(e.extra)
    #             http_status = {
    #                 'KYC_REQUIRED':   status.HTTP_403_FORBIDDEN,
    #                 'PROVIDER_ERROR': status.HTTP_503_SERVICE_UNAVAILABLE,
    #             }.get(e.code, status.HTTP_400_BAD_REQUEST)
    #             return Response(payload, status=http_status)

    #     else:
    #         return Response(
    #             {
    #                 'error': True,
    #                 'code': 'MISSING_ACCOUNT',
    #                 'message': (
    #                     'Provide either a saved_account_id, or both bank_code '
    #                     'and account_number for a new account.'
    #                 ),
    #             },
    #             status=status.HTTP_400_BAD_REQUEST,
    #         )

    #     # ── Create the withdrawal request ────────────────────────────────
    #     try:
    #         withdrawal = WithdrawalService.request(
    #             user=user,
    #             amount=amount,
    #             bank_account=bank_account,    # ← pass the FK
    #         )
    #     except WithdrawalServiceError as e:
    #         return Response(
    #             {'error': True, 'code': 'WITHDRAWAL_REJECTED', 'message': str(e)},
    #             status=status.HTTP_400_BAD_REQUEST,
    #         )

    #     return Response(
    #         {'success': True, 'data': WithdrawalResponseSerializer(withdrawal).data},
    #         status=status.HTTP_201_CREATED,
    #     )

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
    
class CryptoWalletListCreateView(APIView):
    """
    GET  /api/v1/crypto-wallets/      — list user's saved wallets
    POST /api/v1/crypto-wallets/      — save a new wallet
    """
    permission_classes = [IsAuthenticated]
 
    def get(self, request):
        wallets = CryptoWalletService.list_for_user(request.user)
        return Response({
            'success': True,
            'data': {
                'wallets': CryptoWalletSerializer(wallets, many=True).data,
            },
        })
 
    def post(self, request):
        ser = CreateCryptoWalletSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
 
        try:
            wallet = CryptoWalletService.save(
                user=request.user,
                address=ser.validated_data['address'],
                network=ser.validated_data.get('network', 'TRC20'),
                label=ser.validated_data.get('label', ''),
                set_default=ser.validated_data.get('set_default', False),
            )
        except CryptoWalletError as e:
            return Response(
                {'error': True,
                 'code': getattr(e, 'code', 'CRYPTO_WALLET_ERROR'),
                 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
 
        return Response(
            {'success': True, 'data': CryptoWalletSerializer(wallet).data},
            status=status.HTTP_201_CREATED,
        )
 
 
class CryptoWalletDetailView(APIView):
    """
    DELETE /api/v1/crypto-wallets/<id>/  — soft-delete a wallet
    PATCH  /api/v1/crypto-wallets/<id>/  — set as default
    """
    permission_classes = [IsAuthenticated]
 
    def delete(self, request, wallet_id):
        try:
            CryptoWalletService.deactivate(request.user, wallet_id)
            return Response({'success': True}, status=status.HTTP_200_OK)
        except CryptoWalletError as e:
            return Response(
                {'error': True,
                 'code': getattr(e, 'code', 'CRYPTO_WALLET_ERROR'),
                 'message': str(e)},
                status=status.HTTP_404_NOT_FOUND,
            )
 
    def patch(self, request, wallet_id):
        # Only supports "make default" right now
        if request.data.get('action') == 'set_default':
            try:
                wallet = CryptoWalletService.set_default(request.user, wallet_id)
                return Response({
                    'success': True,
                    'data': CryptoWalletSerializer(wallet).data,
                })
            except CryptoWalletError as e:
                return Response(
                    {'error': True,
                     'code': getattr(e, 'code', 'CRYPTO_WALLET_ERROR'),
                     'message': str(e)},
                    status=status.HTTP_404_NOT_FOUND,
                )
        return Response(
            {'error': True, 'code': 'UNKNOWN_ACTION',
             'message': "Only action='set_default' supported."},
            status=status.HTTP_400_BAD_REQUEST,
        )