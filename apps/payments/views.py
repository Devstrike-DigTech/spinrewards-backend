"""
User-facing payment endpoints.

  POST /api/v1/deposits/                  Initiate a deposit
  GET  /api/v1/deposits/                  List the user's deposits
  GET  /api/v1/deposits/<id>/             Get one deposit's status
  GET  /api/v1/deposits/virtual-account/  Get/create the user's Monnify VA
"""
import logging

from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.payments.providers.nowpayments import NOWPaymentsProvider
from decimal import Decimal
from apps.settings_app.services import get_setting
from apps.settings_app.models import SettingKey
from common.exceptions import ProviderError

from .models import Deposit
from .providers.monnify import MonnifyProvider
from .serializers import (
    DepositRequestSerializer,
    DepositSerializer,
    VirtualAccountSerializer,
)
from .services import PaymentService
from django.shortcuts import render
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


class DepositInitiateView(APIView):
    """POST /api/v1/deposits/  — start a new deposit"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        provider_name = request.data.get('provider', '').lower()
        amount_raw = request.data.get('amount')
 
        # Parse amount
        try:
            amount = Decimal(str(amount_raw))
        except Exception:
            return Response(
                {'error': True, 'code': 'INVALID_AMOUNT',
                 'message': 'amount must be a number.'},
                status=400,
            )
 
        # ─── Min deposit enforcement ──────────────────────────────────────
        if provider_name == 'paystack':
            min_ngn = get_setting(SettingKey.MIN_DEPOSIT_NGN)
            if amount < min_ngn:
                return Response(
                    {
                        'error': True,
                        'code': 'BELOW_MIN_DEPOSIT',
                        'message': f'Minimum NGN deposit is ₦{min_ngn}.',
                        'min_amount': str(min_ngn),
                        'currency': 'NGN',
                    },
                    status=400,
                )
        # elif provider_name == 'nowpayments':
        #     # `amount` here is in USD (USDT equivalent) for the crypto flow
        #     min_usd = get_setting(SettingKey.MIN_DEPOSIT_USD)
        #     ngn_per_usd = get_setting(SettingKey.NGN_PER_USD_DISPLAY_RATE)
        #     usd_equivalent = amount / ngn_per_usd
        #     if usd_equivalent < min_usd:
        #         min_ngn = min_usd * ngn_per_usd
        #         return Response({
        #             'error': True,
        #             'code': 'BELOW_MIN_DEPOSIT',
        #             'message': f'Minimum crypto deposit is ₦{min_ngn:.0f} (≈ ${min_usd}).',
        #             'min_amount': str(min_ngn),
        #             'currency': 'NGN',
        #         }, status=400)
        #     if amount < min_usd:
        #         return Response(
        #             {
        #                 'error': True,
        #                 'code': 'BELOW_MIN_DEPOSIT',
        #                 'message': f'Minimum crypto deposit is ${min_usd}.',
        #                 'min_amount': str(min_usd),
        #                 'currency': 'USD',
        #             },
        #             status=400,
        #         )
        elif provider_name == 'nowpayments':
            # amount is in USD
            min_usd = get_setting(SettingKey.MIN_DEPOSIT_USD)
            if amount < min_usd:
                return Response({
                    'error': True,
                    'code': 'BELOW_MIN_DEPOSIT',
                    'message': f'Minimum crypto deposit is ${min_usd}.',
                    'min_amount': str(min_usd),
                    'currency': 'USD',
                }, status=400)

        else:
            return Response({
                'error': True,
                'code': 'UNKNOWN_PROVIDER',
                'message': f'Unknown provider: {provider_name}',
            }, status=400)
        serializer = DepositRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        deposit = PaymentService.initiate_deposit(
            user=request.user,
            amount=serializer.validated_data['amount'],
            provider=serializer.validated_data['provider'],
        )

        return Response(
            {'success': True, 'data': DepositSerializer(deposit).data},
            status=status.HTTP_201_CREATED,
        )


class DepositListView(ListAPIView):
    """GET /api/v1/deposits/  — user's deposit history"""
    permission_classes = [IsAuthenticated]
    serializer_class = DepositSerializer

    def get_queryset(self):
        return Deposit.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})


class DepositDetailView(APIView):
    """GET /api/v1/deposits/<uuid>/  — single deposit status"""
    permission_classes = [IsAuthenticated]

    def get(self, request, deposit_id):
        deposit = Deposit.objects.filter(
            id=deposit_id, user=request.user,
        ).first()
        if not deposit:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Deposit not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({'success': True, 'data': DepositSerializer(deposit).data})


class VirtualAccountView(APIView):
    """
    GET /api/v1/deposits/virtual-account/

    Returns (and creates if needed) the user's permanent Monnify virtual
    account. Frontend displays this for bank transfer deposits.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            va = MonnifyProvider().get_or_create_virtual_account(request.user)
        except ProviderError as e:
            return Response(
                {'error': True, 'code': 'PROVIDER_ERROR', 'message': str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(
            {'success': True, 'data': VirtualAccountSerializer(va).data},
        )

@require_GET
def payment_callback_page(request):
    """
    Public landing page that Paystack redirects to after payment.
    
    Doesn't validate anything — the webhook handles balance crediting.
    This page just closes itself so the user returns to the Mini App.
    """
    status = request.GET.get('status', 'success')
    reference = request.GET.get('reference', '')
    
    return render(request, 'payments/payment_complete.html', {
        'status': status,
        'reference': reference,
    })

class CryptoCurrenciesListView(APIView):
    """
    GET /api/v1/payments/crypto/currencies/
 
    Returns the list of supported crypto currencies for deposits.
    Dynamic from NowPayments. Cached server-side for 1 hour.
    """
    permission_classes = [IsAuthenticated]
 
    def get(self, request):
        try:
            currencies = NOWPaymentsProvider.list_currencies()
        except ProviderError as e:
            return Response(
                {'error': True, 'code': 'PROVIDER_ERROR',
                 'message': f'Unable to fetch currencies: {e}'},
                status=503,
            )
        return Response({'success': True, 'data': {'currencies': currencies}})