from rest_framework import serializers
from .models import KYC


class KYCSubmitSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=200)
    nin = serializers.RegexField(
        regex=r'^\d{11}$',
        error_messages={'invalid': 'NIN must be exactly 11 digits.'},
    )
    dob = serializers.DateField()
    bank_account = serializers.RegexField(
        regex=r'^\d{10}$',
        error_messages={'invalid': 'Bank account must be 10 digits.'},
    )
    bank_code = serializers.CharField(max_length=10)


class KYCStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = KYC
        fields = ['status', 'rejection_reason', 'submitted_at', 'reviewed_at']
        read_only_fields = fields
