from decimal import Decimal
from rest_framework import serializers
from .models import RTPTier, RTPOutcome, SpinResult


class SpinRequestSerializer(serializers.Serializer):
    stake = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=Decimal('1'))
    idempotency_key = serializers.CharField(max_length=200)


class RTPOutcomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RTPOutcome
        fields = ['id', 'label', 'multiplier', 'probability', 'is_active']


class RTPTierPublicSerializer(serializers.ModelSerializer):
    """Public-facing tier info — does NOT expose probability distributions."""
    class Meta:
        model = RTPTier
        fields = ['id', 'name', 'stake_min', 'stake_max', 'rtp_target']


class SpinResultSerializer(serializers.ModelSerializer):
    result = serializers.CharField(source='result')

    class Meta:
        model = SpinResult
        fields = [
            'id', 'stake', 'multiplier', 'win_amount',
            'outcome_label', 'result', 'created_at',
        ]
        read_only_fields = fields
