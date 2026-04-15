from decimal import Decimal
from rest_framework import serializers
from .models import DepositSession


class DepositRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, min_value=Decimal('100')
    )
    method = serializers.ChoiceField(choices=DepositSession.Method.choices)


class DepositSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DepositSession
        fields = [
            'id', 'amount', 'method', 'payment_url',
            'provider_reference', 'status', 'created_at',
        ]
        read_only_fields = fields
