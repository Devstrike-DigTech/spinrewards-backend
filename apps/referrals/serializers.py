from rest_framework import serializers
from .models import Referral


class ReferralInfoSerializer(serializers.Serializer):
    referral_code = serializers.CharField()
    referral_link = serializers.CharField()
    total_referrals = serializers.IntegerField()
    converted_referrals = serializers.IntegerField()
    total_earned = serializers.DecimalField(max_digits=15, decimal_places=2)


class ReferralListSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='referred_user.username')
    first_name = serializers.CharField(source='referred_user.first_name')
    bonus_earned = serializers.DecimalField(
        source='referrer_bonus_amount', max_digits=15, decimal_places=2
    )
    joined_at = serializers.DateTimeField(source='created_at')

    class Meta:
        model = Referral
        fields = ['username', 'first_name', 'status', 'bonus_earned', 'joined_at']
