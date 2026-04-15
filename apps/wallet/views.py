from rest_framework.views import APIView
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from .services import WalletService
from .models import Transaction
from .serializers import TransactionSerializer


class WalletView(APIView):
    """GET /api/v1/wallet/ — Get wallet balances."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        summary = WalletService.get_wallet_summary(request.user)
        return Response({'success': True, 'data': summary})


class TransactionListView(ListAPIView):
    """GET /api/v1/wallet/transactions/ — Get transaction history."""
    permission_classes = [IsAuthenticated]
    serializer_class = TransactionSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['type', 'balance_type', 'status']

    def get_queryset(self):
        return Transaction.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})
