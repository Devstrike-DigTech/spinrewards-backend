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

from .models import CryptoWallet, Withdrawal


class WithdrawalRequestSerializer(serializers.Serializer):
    # amount = serializers.DecimalField(
    #     max_digits=20, decimal_places=2, required=True, min_value=Decimal('1000'),
    # )
    """
    POST /api/v1/withdrawals/
 
    Bank rail:
      { "rail": "bank", "amount": 5000, "saved_account_id": "<uuid>" }
      or { "rail": "bank", "amount": 5000, "bank_code": "058", "account_number": "0123456789" }
 
    Crypto rail:
      { "rail": "crypto", "amount": 20, "wallet_address": "T...", "network": "TRC20" }
      or { "rail": "crypto", "amount": 20, "saved_wallet_id": "<uuid>" }
 
    The amount is in the rail's currency (NGN for bank, USDT for crypto).
    Min amounts come from settings (MIN_WITHDRAWAL_NGN / MIN_WITHDRAWAL_USDT)
    and are enforced server-side; the serializer just checks > 0.
    """
    rail = serializers.ChoiceField(
        choices=[
            ('bank', 'Bank Transfer (NGN)'),
            ('crypto', 'Crypto (USDT TRC-20)'),
        ],
        default='bank',
        required=False,
    )
    amount = serializers.DecimalField(
        max_digits=20, decimal_places=2, required=True,
        min_value=Decimal('0.000001'),
    )
 
    # Bank rail fields
    saved_account_id = serializers.UUIDField(required=False, allow_null=True)
    bank_code = serializers.CharField(required=False, allow_blank=True, default='')
    account_number = serializers.CharField(required=False, allow_blank=True, default='')
 
    # Crypto rail fields
    saved_wallet_id = serializers.UUIDField(required=False, allow_null=True)
    wallet_address = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=120,
    )
    network = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=20,
    )
 
    def validate(self, data):
        rail = data.get('rail', 'bank')
 
        if rail == 'bank':
            # Either saved_account_id OR (bank_code + account_number) required
            has_saved = bool(data.get('saved_account_id'))
            has_inline = bool(data.get('bank_code') and data.get('account_number'))
            if not (has_saved or has_inline):
                raise serializers.ValidationError({
                    'bank_account': (
                        'Provide either saved_account_id OR '
                        'bank_code + account_number.'
                    ),
                })
 
        elif rail == 'crypto':
            # Either saved_wallet_id OR wallet_address required
            has_saved = bool(data.get('saved_wallet_id'))
            has_inline = bool(data.get('wallet_address'))
            if not (has_saved or has_inline):
                raise serializers.ValidationError({
                    'wallet_address': (
                        'Provide either saved_wallet_id OR wallet_address.'
                    ),
                })
 
        return data


class WithdrawalResponseSerializer(serializers.ModelSerializer):
    # bank_account = BankAccountResponseSerializer(read_only=True)
    # amount = serializers.DecimalField(
    #     max_digits=20, decimal_places=2, coerce_to_string=True,
    # )
    # fee = serializers.DecimalField(
    #     max_digits=20, decimal_places=2, coerce_to_string=True,
    # )
    # net_amount = serializers.DecimalField(
    #     max_digits=20, decimal_places=2, coerce_to_string=True,
    # )

    # class Meta:
    #     model = Withdrawal
    #     fields = [
    #         'id',
    #         'bank_account',
    #         'amount',
    #         'fee',
    #         'net_amount',
    #         'status',
    #         'reference',
    #         'requires_review',
    #         'forced_manual_review',
    #         'failure_reason',
    #         'requested_at',
    #         'completed_at',
    #     ]
    #     read_only_fields = fields
    bank_account = BankAccountResponseSerializer(read_only=True, allow_null=True)
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
            'reference',
            'rail',                  
            'currency',              
            'amount',
            'fee',
            'net_amount',
            'status',
            'requires_review',
 
            # Bank rail
            'bank_account',         
 
            # Crypto rail
            'wallet_address',        
            'network',              
            'tx_hash',              
            # Timestamps
            'requested_at',
            'processing_at',
            'completed_at',
            'failure_reason',
        ]
        read_only_fields = fields


class CryptoWalletSerializer(serializers.ModelSerializer):
    """Response shape for /crypto-wallets/ endpoints."""
 
    address_masked = serializers.SerializerMethodField()
 
    class Meta:
        model = CryptoWallet
        fields = [
            'id',
            'network',
            'address',
            'address_masked',
            'label',
            'is_default',
            'is_active',
            'verified_at',
            'created_at',
        ]
        read_only_fields = [
            'id', 'address_masked', 'is_active', 'verified_at', 'created_at',
        ]
 
    def get_address_masked(self, obj):
        """Return e.g. 'TYxxxx...abc123' for safer display."""
        addr = obj.address
        if len(addr) < 16:
            return addr
        return f'{addr[:6]}...{addr[-6:]}'
 
 
class CreateCryptoWalletSerializer(serializers.Serializer):
    """Body for POST /api/v1/crypto-wallets/"""
    address = serializers.CharField(max_length=120, required=True)
    network = serializers.CharField(
        max_length=20, required=False, default='TRC20',
    )
    label = serializers.CharField(
        max_length=50, required=False, allow_blank=True, default='',
    )
    set_default = serializers.BooleanField(required=False, default=False)