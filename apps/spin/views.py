from decimal import Decimal
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from .services import SpinService
from .models import RTPTier, SpinResult
from .serializers import (
    SpinRequestSerializer,
    RTPTierPublicSerializer,
    SpinResultSerializer,
)


class SpinView(APIView):
    """POST /api/v1/spin/ — Execute a spin."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = SpinRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = SpinService.execute(
            user=request.user,
            stake=serializer.validated_data['stake'],
            idempotency_key=serializer.validated_data['idempotency_key'],
        )

        return Response({'success': True, 'data': result}, status=status.HTTP_200_OK)


class SpinTiersView(APIView):
    """GET /api/v1/spin/tiers/ — Get available stake tiers (public info only)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tiers = RTPTier.objects.filter(is_active=True).order_by('stake_min')
        return Response({
            'success': True,
            'data': {'tiers': RTPTierPublicSerializer(tiers, many=True).data},
        })


class SpinHistoryView(ListAPIView):
    """GET /api/v1/spin/history/ — Get user spin history."""
    permission_classes = [IsAuthenticated]
    serializer_class = SpinResultSerializer

    def get_queryset(self):
        return SpinResult.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})
