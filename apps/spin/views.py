"""
Spin engine HTTP endpoints.

  POST /api/v1/spin/                        Execute a spin
  POST /api/v1/spin/welcome/                Execute the user's free welcome spin
  GET  /api/v1/spin/wheels/                 List ALL wheels (active + inactive)
  GET  /api/v1/spin/wheels/active/          List only active wheels
  GET  /api/v1/spin/wheels/for-stake/       Find the wheel for a stake amount
  GET  /api/v1/spin/history/                List the user's past spins

Rate limiting: 60 spins per hour per user (PRD requirement).
"""
from decimal import Decimal, InvalidOperation

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
    """POST /api/v1/spin/welcome/"""
    permission_classes = [IsAuthenticated]
    throttle_classes = [SpinThrottle]

    def post(self, request):
        serializer = WelcomeSpinRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

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
            stake_amount=None,
            client_seed=serializer.validated_data.get('client_seed', ''),
        )

        return Response(
            {'success': True, 'data': SpinPublicSerializer(spin).data},
            status=status.HTTP_200_OK,
        )


class WheelListView(APIView):
    """
    GET /api/v1/spin/wheels/

    Returns ALL wheels (active + inactive). For users, prefer
    /wheels/active/ which filters automatically.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wheels = Wheel.objects.all().order_by('min_stake')

        # Hide welcome wheel from list if user has already used it
        already_used_welcome = Spin.objects.filter(
            user=request.user, is_welcome_spin=True,
        ).exists()
        if already_used_welcome:
            wheels = wheels.exclude(wheel_type=Wheel.WheelType.WELCOME)

        return Response({
            'success': True,
            'data': {'wheels': WheelPublicSerializer(wheels, many=True).data},
        })


class ActiveWheelListView(APIView):
    """
    GET /api/v1/spin/wheels/active/

    Returns only active wheels. This is what the frontend uses to show
    available stake options. Welcome wheel is excluded for users who've
    already used it.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wheels = Wheel.objects.filter(is_active=True).order_by('min_stake')

        already_used_welcome = Spin.objects.filter(
            user=request.user, is_welcome_spin=True,
        ).exists()
        if already_used_welcome:
            wheels = wheels.exclude(wheel_type=Wheel.WheelType.WELCOME)

        return Response({
            'success': True,
            'data': {'wheels': WheelPublicSerializer(wheels, many=True).data},
        })


class WheelForStakeView(APIView):
    """
    GET /api/v1/spin/wheels/for-stake/?amount=500

    Returns the active wheel that matches the given stake amount.
    Range semantics: inclusive lower, exclusive upper.

    Returns 404 if no wheel matches (stake outside all configured ranges).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        amount_str = request.query_params.get('amount')
        if not amount_str:
            return Response(
                {'error': True, 'code': 'VALIDATION_ERROR',
                 'message': 'Query parameter "amount" is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            amount = Decimal(amount_str)
        except (InvalidOperation, ValueError):
            return Response(
                {'error': True, 'code': 'VALIDATION_ERROR',
                 'message': f'Invalid amount: {amount_str}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if amount <= 0:
            return Response(
                {'error': True, 'code': 'VALIDATION_ERROR',
                 'message': 'Amount must be positive.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        wheel = SpinEngine.find_wheel_for_stake(amount)
        if not wheel:
            return Response(
                {'error': True, 'code': 'NO_WHEEL_FOR_STAKE',
                 'message': f'No active wheel matches stake ₦{amount}.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            'success': True,
            'data': {'wheel': WheelPublicSerializer(wheel).data},
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