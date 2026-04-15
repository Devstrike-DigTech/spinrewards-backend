from decimal import Decimal
from rest_framework import serializers
from apps.spin.models import RTPTier, RTPOutcome
from apps.users.models import User
from apps.kyc.models import KYC
from apps.withdrawals.models import WithdrawalRequest
from .models import AdminAuditLog


# ── RTP Serializers ────────────────────────────────────────────────────────────

class RTPOutcomeAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = RTPOutcome
        fields = ['id', 'label', 'multiplier', 'probability', 'is_active']


class RTPTierAdminSerializer(serializers.ModelSerializer):
    outcomes = RTPOutcomeAdminSerializer(many=True, read_only=True)
    computed_rtp = serializers.SerializerMethodField()

    class Meta:
        model = RTPTier
        fields = [
            'id', 'name', 'stake_min', 'stake_max', 'rtp_target',
            'is_active', 'outcomes', 'computed_rtp', 'created_at', 'updated_at',
        ]

    def get_computed_rtp(self, obj):
        return f'{obj.computed_rtp:.2f}'


class RTPOutcomeCreateSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=50)
    multiplier = serializers.DecimalField(max_digits=10, decimal_places=4, min_value=Decimal('0'))
    probability = serializers.DecimalField(max_digits=7, decimal_places=4, min_value=Decimal('0'))


class RTPTierCreateSerializer(serializers.ModelSerializer):
    outcomes = RTPOutcomeCreateSerializer(many=True, required=False)

    class Meta:
        model = RTPTier
        fields = ['name', 'stake_min', 'stake_max', 'rtp_target', 'is_active', 'outcomes']

    def validate(self, attrs):
        outcomes = attrs.get('outcomes', [])
        if outcomes:
            total = sum(o['probability'] for o in outcomes)
            if abs(total - Decimal('100')) > Decimal('0.01'):
                raise serializers.ValidationError(
                    f'Outcome probabilities must sum to 100%. Got {total}%.'
                )
        return attrs

    def create(self, validated_data):
        outcomes_data = validated_data.pop('outcomes', [])
        tier = RTPTier.objects.create(**validated_data)
        for outcome in outcomes_data:
            RTPOutcome.objects.create(tier=tier, **outcome)
        return tier

    def update(self, instance, validated_data):
        validated_data.pop('outcomes', None)  # Outcomes updated separately
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


# ── User Serializers ───────────────────────────────────────────────────────────

class AdminUserSerializer(serializers.ModelSerializer):
    kyc_status = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'telegram_id', 'username', 'first_name', 'last_name',
            'kyc_status', 'is_active', 'is_admin', 'created_at',
        ]

    def get_kyc_status(self, obj):
        return obj.kyc.status if hasattr(obj, 'kyc') else 'unverified'


# ── KYC Serializers ────────────────────────────────────────────────────────────

class AdminKYCSerializer(serializers.ModelSerializer):
    user = AdminUserSerializer(read_only=True)
    bank_account_masked = serializers.SerializerMethodField()

    class Meta:
        model = KYC
        fields = [
            'id', 'user', 'full_name', 'dob', 'bank_account_masked',
            'bank_code', 'account_name', 'document_type', 'document_url',
            'status', 'rejection_reason', 'submitted_at', 'reviewed_at',
        ]

    def get_bank_account_masked(self, obj):
        acct = obj.bank_account
        return f'****{acct[-4:]}' if len(acct) >= 4 else acct


# ── Withdrawal Serializers ─────────────────────────────────────────────────────

class AdminWithdrawalSerializer(serializers.ModelSerializer):
    user = AdminUserSerializer(read_only=True)

    class Meta:
        model = WithdrawalRequest
        fields = [
            'id', 'user', 'amount', 'bank_account', 'bank_code',
            'account_name', 'reference', 'status', 'failure_reason',
            'created_at', 'processed_at',
        ]


# ── Audit Log Serializer ───────────────────────────────────────────────────────

class AdminAuditLogSerializer(serializers.ModelSerializer):
    admin = serializers.SerializerMethodField()

    class Meta:
        model = AdminAuditLog
        fields = [
            'id', 'admin', 'action', 'target_model', 'target_id',
            'previous_state', 'new_state', 'created_at',
        ]

    def get_admin(self, obj):
        if obj.admin:
            return {'id': str(obj.admin.id), 'username': obj.admin.username}
        return None
