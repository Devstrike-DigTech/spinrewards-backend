from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.payments.services import PaymentService
from .services import WithdrawalService
from .models import WithdrawalRequest
from .serializers import WithdrawalRequestSerializer, WithdrawalResponseSerializer


class WithdrawalView(APIView):
    """POST /api/v1/withdrawals/ — Request a withdrawal."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WithdrawalRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # Resolve bank account name before proceeding
        account_name = PaymentService.resolve_bank_account(
            data['bank_account'], data['bank_code']
        )

        withdrawal = WithdrawalService.request(
            user=request.user,
            amount=data['amount'],
            bank_account=data['bank_account'],
            bank_code=data['bank_code'],
            account_name=account_name,
        )

        return Response(
            {'success': True, 'data': WithdrawalResponseSerializer(withdrawal).data},
            status=status.HTTP_201_CREATED,
        )


class WithdrawalListView(ListAPIView):
    """GET /api/v1/withdrawals/ — Get withdrawal history."""
    permission_classes = [IsAuthenticated]
    serializer_class = WithdrawalResponseSerializer

    def get_queryset(self):
        return WithdrawalRequest.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})


class WithdrawalDetailView(RetrieveAPIView):
    """GET /api/v1/withdrawals/{id}/ — Get withdrawal detail."""
    permission_classes = [IsAuthenticated]
    serializer_class = WithdrawalResponseSerializer

    def get_queryset(self):
        return WithdrawalRequest.objects.filter(user=self.request.user)

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})
