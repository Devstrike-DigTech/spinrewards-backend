# from decimal import Decimal
# from rest_framework.views import APIView
# from rest_framework.generics import ListAPIView
# from rest_framework.permissions import IsAuthenticated
# from rest_framework.response import Response
# from rest_framework import status

# from .services import SpinService
# from .models import RTPTier, SpinResult
# from .serializers import (
#     SpinRequestSerializer,
#     RTPTierPublicSerializer,
#     SpinResultSerializer,
# )


# class SpinView(APIView):
#     """POST /api/v1/spin/ — Execute a spin."""
#     permission_classes = [IsAuthenticated]

#     def post(self, request):
#         serializer = SpinRequestSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)

#         result = SpinService.execute(
#             user=request.user,
#             stake=serializer.validated_data['stake'],
#             idempotency_key=serializer.validated_data['idempotency_key'],
#         )

#         return Response({'success': True, 'data': result}, status=status.HTTP_200_OK)


# class SpinTiersView(APIView):
#     """GET /api/v1/spin/tiers/ — Get available stake tiers (public info only)."""
#     permission_classes = [IsAuthenticated]

#     def get(self, request):
#         tiers = RTPTier.objects.filter(is_active=True).order_by('stake_min')
#         return Response({
#             'success': True,
#             'data': {'tiers': RTPTierPublicSerializer(tiers, many=True).data},
#         })


# class SpinHistoryView(ListAPIView):
#     """GET /api/v1/spin/history/ — Get user spin history."""
#     permission_classes = [IsAuthenticated]
#     serializer_class = SpinResultSerializer

#     def get_queryset(self):
#         return SpinResult.objects.filter(user=self.request.user)

#     def list(self, request, *args, **kwargs):
#         response = super().list(request, *args, **kwargs)
#         return Response({'success': True, 'data': response.data})

"""
Spin engine HTTP endpoints.

  POST /api/v1/spin/                  Execute a spin (stake-based)
  POST /api/v1/spin/welcome/          Execute the user's free welcome spin
  GET  /api/v1/spin/wheels/           List active wheels (public-safe info)
  GET  /api/v1/spin/history/          List the user's past spins

Rate limiting: 60 spins per hour per user (PRD requirement). Configured
in settings via the 'spin' throttle scope.
"""
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .models import Spin, Wheel
from .serializers import (
    SpinPublicSerializer,
    SpinRequestSerializer,
    WheelPublicSerializer,
    WelcomeSpinRequestSerializer,
)
from .services import SpinEngine


class SpinThrottle(UserRateThrottle):
    """60 per hour per user. Backed by Redis in production."""
    scope = 'spin'


class SpinExecuteView(APIView):
    """POST /api/v1/spin/"""
    permission_classes = [IsAuthenticated]
    throttle_classes = [SpinThrottle]

    def post(self, request):
        serializer = SpinRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        spin = SpinEngine.execute(
            user=request.user,
            wheel_id=serializer.validated_data['wheel_id'],
            stake_amount=serializer.validated_data['stake_amount'],
            client_seed=serializer.validated_data.get('client_seed', ''),
        )

        return Response(
            {'success': True, 'data': SpinPublicSerializer(spin).data},
            status=status.HTTP_200_OK,
        )


class WelcomeSpinView(APIView):
    """
    POST /api/v1/spin/welcome/

    Free, one-time welcome spin for new users.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [SpinThrottle]

    def post(self, request):
        serializer = WelcomeSpinRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Find the welcome wheel
        welcome_wheel = Wheel.objects.filter(
            wheel_type=Wheel.WheelType.WELCOME,
            is_active=True,
        ).first()
        if not welcome_wheel:
            return Response(
                {'error': True, 'code': 'NO_WELCOME_WHEEL',
                 'message': 'Welcome wheel is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        spin = SpinEngine.execute(
            user=request.user,
            wheel_id=str(welcome_wheel.id),
            stake_amount=None,  # ignored; welcome is free
            client_seed=serializer.validated_data.get('client_seed', ''),
        )

        return Response(
            {'success': True, 'data': SpinPublicSerializer(spin).data},
            status=status.HTTP_200_OK,
        )


class WheelListView(APIView):
    """
    GET /api/v1/spin/wheels/

    Returns active wheels with public-safe info: type, name, stake range,
    currency. Probability distributions are NOT exposed.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wheels = Wheel.objects.filter(is_active=True).order_by('min_stake')
        # Hide welcome wheel from the list if user has already used it.
        already_used_welcome = Spin.objects.filter(
            user=request.user, is_welcome_spin=True,
        ).exists()
        if already_used_welcome:
            wheels = wheels.exclude(wheel_type=Wheel.WheelType.WELCOME)

        return Response({
            'success': True,
            'data': {'wheels': WheelPublicSerializer(wheels, many=True).data},
        })


class SpinHistoryView(ListAPIView):
    """GET /api/v1/spin/history/"""
    permission_classes = [IsAuthenticated]
    serializer_class = SpinPublicSerializer

    def get_queryset(self):
        return Spin.objects.filter(user=self.request.user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})