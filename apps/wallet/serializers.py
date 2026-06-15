# from rest_framework import serializers
# from .models import Transaction


# class WalletSerializer(serializers.Serializer):
#     coin_balance = serializers.DecimalField(max_digits=15, decimal_places=2)
#     cash_balance = serializers.DecimalField(max_digits=15, decimal_places=2)
#     total_balance = serializers.DecimalField(max_digits=15, decimal_places=2)


# class TransactionSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Transaction
#         fields = [
#             'id', 'type', 'balance_type', 'amount',
#             'balance_before', 'balance_after',
#             'reference_id', 'status', 'created_at',
#         ]
#         read_only_fields = fields

from rest_framework import serializers

from .models import Transaction


class WalletSerializer(serializers.Serializer):
    """Used to validate the response shape from get_wallet_summary()."""
    coin_balance = serializers.DecimalField(max_digits=20, decimal_places=2)
    cash_balance = serializers.DecimalField(max_digits=20, decimal_places=2)
    staked_balance = serializers.DecimalField(max_digits=20, decimal_places=2)
    total_balance = serializers.DecimalField(max_digits=20, decimal_places=2)


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = [
            'id', 'type', 'balance_type', 'amount',
            'balance_before', 'balance_after',
            'reference_id', 'status', 'currency', 'metadata', 'created_at',
        ]
        read_only_fields = fields