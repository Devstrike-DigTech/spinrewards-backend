from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .services import RewardsService


class DailyRewardStatusView(APIView):
    """GET /api/v1/rewards/daily/ — Get daily reward status."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        status = RewardsService.get_status(request.user)
        return Response({'success': True, 'data': status})


class DailyRewardClaimView(APIView):
    """POST /api/v1/rewards/daily/claim/ — Claim daily reward."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        result = RewardsService.claim(request.user)
        return Response({'success': True, 'data': result})
