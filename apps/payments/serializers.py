# from decimal import Decimal
# from rest_framework import serializers
# from .models import DepositSession


# class DepositRequestSerializer(serializers.Serializer):
#     amount = serializers.DecimalField(
#         max_digits=15, decimal_places=2, min_value=Decimal('100')
#     )
#     method = serializers.ChoiceField(choices=DepositSession.Method.choices)


# class DepositSessionSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = DepositSession
#         fields = [
#             'id', 'amount', 'method', 'payment_url',
#             'provider_reference', 'status', 'created_at',
#         ]
#         read_only_fields = fields

from decimal import Decimal

from rest_framework import serializers

from .models import Deposit, VirtualAccount


class DepositRequestSerializer(serializers.Serializer):
    """Body for POST /deposits/"""
    amount = serializers.DecimalField(
        max_digits=20, decimal_places=2, min_value=Decimal('100'),
    )
    provider = serializers.ChoiceField(choices=Deposit.Provider.choices)


class DepositSerializer(serializers.ModelSerializer):
    class Meta:
        model = Deposit
        fields = [
            'id',
            'amount',
            'provider',
            'internal_reference',
            'provider_reference',
            'payment_url',
            'payment_address',
            'original_amount',
            'original_currency',
            'conversion_rate',
            'status',
            'created_at',
            'completed_at',
        ]
        read_only_fields = fields


class VirtualAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = VirtualAccount
        fields = [
            'id',
            'account_number',
            'account_name',
            'bank_name',
            'bank_code',
            'is_active',
            'created_at',
        ]
        read_only_fields = fields