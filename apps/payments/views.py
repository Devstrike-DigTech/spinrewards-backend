from rest_framework.views import APIView
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .services import PaymentService
from .models import DepositSession
from .serializers import DepositRequestSerializer, DepositSessionSerializer


class DepositView(APIView):
    """POST /api/v1/deposits/ — Initiate a deposit."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = DepositRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session = PaymentService.initiate_deposit(
            user=request.user,
            amount=serializer.validated_data['amount'],
            method=serializer.validated_data['method'],
        )

        return Response(
            {'success': True, 'data': DepositSessionSerializer(session).data},
            status=status.HTTP_201_CREATED,
        )


class DepositListView(ListAPIView):
    """GET /api/v1/deposits/ — Get deposit history."""
    permission_classes = [IsAuthenticated]
    serializer_class = DepositSessionSerializer

    def get_queryset(self):
        return DepositSession.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})
