from rest_framework import serializers
from .models import User


class UserSerializer(serializers.ModelSerializer):
    kyc_status = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'telegram_id', 'username', 'first_name', 'last_name',
            'kyc_status', 'referral_code', 'created_at',
        ]
        read_only_fields = fields

    def get_kyc_status(self, obj):
        if hasattr(obj, 'kyc'):
            return obj.kyc.status
        return 'unverified'


class TelegramAuthSerializer(serializers.Serializer):
    init_data = serializers.CharField(required=True)


class TokenRefreshSerializer(serializers.Serializer):
    refresh_token = serializers.CharField(required=True)


class AuthResponseSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    refresh_token = serializers.CharField()
    token_type = serializers.CharField(default='Bearer')
    user = UserSerializer()
