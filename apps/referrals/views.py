from decimal import Decimal
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import Referral
from .serializers import ReferralInfoSerializer, ReferralListSerializer


class ReferralInfoView(APIView):
    """GET /api/v1/referral/ — Get referral info."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        referrals = Referral.objects.filter(referrer=user)
        converted = referrals.filter(status=Referral.Status.CONVERTED)
        total_earned = sum(r.referrer_bonus_amount for r in converted) or Decimal('0')

        bot_username = getattr(settings, 'TELEGRAM_BOT_USERNAME', 'SpinRewardsBot')
        referral_link = f'https://t.me/{bot_username}?start=ref_{user.referral_code}'

        return Response({
            'success': True,
            'data': {
                'referral_code': user.referral_code,
                'referral_link': referral_link,
                'total_referrals': referrals.count(),
                'converted_referrals': converted.count(),
                'total_earned': str(total_earned),
            }
        })


class ReferralListView(ListAPIView):
    """GET /api/v1/referral/list/ — List of referred users."""
    permission_classes = [IsAuthenticated]
    serializer_class = ReferralListSerializer

    def get_queryset(self):
        return Referral.objects.filter(referrer=self.request.user).select_related('referred_user')

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({'success': True, 'data': response.data})
