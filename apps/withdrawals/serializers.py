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
