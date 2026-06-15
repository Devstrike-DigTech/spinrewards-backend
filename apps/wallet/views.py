from rest_framework.views import APIView
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from .services import WalletService
from .models import Transaction
from .serializers import TransactionSerializer


# class WalletView(APIView):
#     """GET /api/v1/wallet/ — Get wallet balances."""
#     permission_classes = [IsAuthenticated]

#     def get(self, request):
#         summary = WalletService.get_wallet_summary(request.user)
#         return Response({'success': True, 'data': summary})
class WalletView(APIView):
    """GET /api/v1/wallet/ — full wallet state + caps + crypto flag."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.settings_app.services import get_setting
        from apps.settings_app.models import SettingKey

        wallet = WalletService.get_wallet_summary(request.user)

        return Response({
            'success': True,
            'data': {
                # ─── v3 canonical (nested) ────────────────────────
                'wallet': wallet,
                'currency_caps': {
                    'min_deposit_ngn': str(get_setting(SettingKey.MIN_DEPOSIT_NGN)),
                    'min_deposit_usd': str(get_setting(SettingKey.MIN_DEPOSIT_USD)),
                    'min_withdrawal_ngn': str(get_setting(SettingKey.MIN_WITHDRAWAL_NGN)),
                    'min_withdrawal_usdt': str(get_setting(SettingKey.MIN_WITHDRAWAL_USDT)),
                },
                'crypto_withdrawal_enabled': bool(
                    get_setting(SettingKey.CRYPTO_WITHDRAWAL_ENABLED) > 0
                ),

                # ─── Backward compat (remove in Chunk 11 cleanup) ─
                **wallet,
            },
        })


# class TransactionListView(ListAPIView):
#     """GET /api/v1/wallet/transactions/ — Get transaction history."""
#     permission_classes = [IsAuthenticated]
#     serializer_class = TransactionSerializer
#     filter_backends = [DjangoFilterBackend]
#     filterset_fields = ['type', 'balance_type', 'status']

#     def get_queryset(self):
#         return Transaction.objects.filter(user=self.request.user)

#     def list(self, request, *args, **kwargs):
#         response = super().list(request, *args, **kwargs)
#         return Response({'success': True, 'data': response.data})

class TransactionListView(ListAPIView):
    """
    GET /api/v1/wallet/transactions/

    Returns the user's transaction history.

    Query params (all optional):
        type           e.g. 'deposit' | 'withdrawal' | 'win' | 'lock' | 'forfeit' | 'refund' | 'bonus'
        balance_type   e.g. 'naira_coins' | 'crypto_coins' | 'bonus_coins' |
                            'naira_withdraw' | 'crypto_withdraw' | 'staked'
        status         e.g. 'completed' | 'pending' | 'failed'
        currency       'NGN' | 'USDT'

    Returns paginated list, most recent first.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = TransactionSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['type', 'balance_type', 'status', 'currency']

    def get_queryset(self):
        return Transaction.objects.filter(
            user=self.request.user,
        ).order_by('-created_at')

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})
