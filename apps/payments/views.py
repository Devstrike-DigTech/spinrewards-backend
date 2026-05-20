# from rest_framework.views import APIView
# from rest_framework.generics import ListAPIView
# from rest_framework.permissions import IsAuthenticated
# from rest_framework.response import Response
# from rest_framework import status

# from .services import PaymentService
# from .models import DepositSession
# from .serializers import DepositRequestSerializer, DepositSessionSerializer


# class DepositView(APIView):
#     """POST /api/v1/deposits/ — Initiate a deposit."""
#     permission_classes = [IsAuthenticated]

#     def post(self, request):
#         serializer = DepositRequestSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)

#         session = PaymentService.initiate_deposit(
#             user=request.user,
#             amount=serializer.validated_data['amount'],
#             method=serializer.validated_data['method'],
#         )

#         return Response(
#             {'success': True, 'data': DepositSessionSerializer(session).data},
#             status=status.HTTP_201_CREATED,
#         )


# class DepositListView(ListAPIView):
#     """GET /api/v1/deposits/ — Get deposit history."""
#     permission_classes = [IsAuthenticated]
#     serializer_class = DepositSessionSerializer

#     def get_queryset(self):
#         return DepositSession.objects.filter(user=self.request.user)

#     def list(self, request, *args, **kwargs):
#         response = super().list(request, *args, **kwargs)
#         return Response({'success': True, 'data': response.data})
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