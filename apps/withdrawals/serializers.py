# from decimal import Decimal
# from rest_framework import serializers
# from .models import WithdrawalRequest


# class WithdrawalRequestSerializer(serializers.Serializer):
#     amount = serializers.DecimalField(
#         max_digits=15, decimal_places=2, min_value=Decimal('100')
#     )
#     bank_account = serializers.RegexField(
#         regex=r'^\d{10}$', error_messages={'invalid': 'Bank account must be 10 digits.'}
#     )
#     bank_code = serializers.CharField(max_length=10)


# class WithdrawalResponseSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = WithdrawalRequest
#         fields = [
#             'id', 'amount', 'bank_account', 'bank_code', 'account_name',
#             'reference', 'status', 'created_at', 'processed_at',
#         ]
#         read_only_fields = fields

"""DRF serializers."""
from decimal import Decimal

from rest_framework import serializers

from apps.kyc.serializers import BankAccountResponseSerializer

from .models import Withdrawal


class WithdrawalRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(
        max_digits=20, decimal_places=2, required=True, min_value=Decimal('1000'),
    )


class WithdrawalResponseSerializer(serializers.ModelSerializer):
    bank_account = BankAccountResponseSerializer(read_only=True)
    amount = serializers.DecimalField(
        max_digits=20, decimal_places=2, coerce_to_string=True,
    )
    fee = serializers.DecimalField(
        max_digits=20, decimal_places=2, coerce_to_string=True,
    )
    net_amount = serializers.DecimalField(
        max_digits=20, decimal_places=2, coerce_to_string=True,
    )

    class Meta:
        model = Withdrawal
        fields = [
            'id',
            'bank_account',
            'amount',
            'fee',
            'net_amount',
            'status',
            'reference',
            'requires_review',
            'forced_manual_review',
            'failure_reason',
            'requested_at',
            'completed_at',
        ]
        read_only_fields = fields