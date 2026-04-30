# from decimal import Decimal
# from rest_framework import serializers
# from .models import RTPTier, RTPOutcome, SpinResult


# class SpinRequestSerializer(serializers.Serializer):
#     stake = serializers.DecimalField(max_digits=15, decimal_places=2, min_value=Decimal('1'))
#     idempotency_key = serializers.CharField(max_length=200)


# class RTPOutcomeSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = RTPOutcome
#         fields = ['id', 'label', 'multiplier', 'probability', 'is_active']


# class RTPTierPublicSerializer(serializers.ModelSerializer):
#     """Public-facing tier info — does NOT expose probability distributions."""
#     class Meta:
#         model = RTPTier
#         fields = ['id', 'name', 'stake_min', 'stake_max', 'rtp_target']


# class SpinResultSerializer(serializers.ModelSerializer):
#     result = serializers.CharField(source='result')

#     class Meta:
#         model = SpinResult
#         fields = [
#             'id', 'stake', 'multiplier', 'win_amount',
#             'outcome_label', 'result', 'created_at',
#         ]
#         read_only_fields = fields

"""
Spin engine serializers.

Public serializers expose only what a client needs and never reveal
probability distributions or server seeds (until the reveal flow is
implemented in v1.5).
"""
from decimal import Decimal

from rest_framework import serializers

from .models import Spin, Wheel


class SpinRequestSerializer(serializers.Serializer):
    """Body for POST /spin/"""
    wheel_id = serializers.UUIDField()
    stake_amount = serializers.DecimalField(
        max_digits=20, decimal_places=2, min_value=Decimal('1'),
    )
    # Client seed is optional and informational only in v1.
    client_seed = serializers.CharField(
        max_length=128, required=False, allow_blank=True, default='',
    )


class WelcomeSpinRequestSerializer(serializers.Serializer):
    """Body for POST /spin/welcome/"""
    client_seed = serializers.CharField(
        max_length=128, required=False, allow_blank=True, default='',
    )


class WheelPublicSerializer(serializers.ModelSerializer):
    """Wheel info safe for clients. Does NOT include segment probabilities."""
    class Meta:
        model = Wheel
        fields = [
            'id', 'wheel_type', 'name', 'currency_type',
            'min_stake', 'max_stake', 'is_welcome_only', 'rtp_target',
        ]
        read_only_fields = fields


class SpinPublicSerializer(serializers.ModelSerializer):
    """Spin info returned to the user."""
    wheel = WheelPublicSerializer(read_only=True)
    segment_label = serializers.CharField(source='segment_landed.label', read_only=True)
    segment_position = serializers.IntegerField(source='segment_landed.position', read_only=True)
    multiplier = serializers.DecimalField(
        source='segment_landed.multiplier',
        max_digits=10, decimal_places=4, read_only=True,
    )

    class Meta:
        model = Spin
        fields = [
            'id', 'wheel', 'stake_amount',
            'segment_label', 'segment_position', 'multiplier',
            'payout_amount', 'outcome',
            # Provably fair: hash exposed (commit), seed NOT exposed yet.
            'server_seed_hash', 'client_seed', 'nonce',
            'is_welcome_spin', 'created_at',
        ]
        read_only_fields = fields