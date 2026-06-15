

"""
Spin engine serializers.

Public-facing serializers ALWAYS hide probability_weight — that's the
platform's house edge configuration, kept private. Other segment fields
(label, position, multiplier, color) are exposed so the frontend can
render wheels accurately.

Spin responses include a nested wheel via WheelPublicSerializer, which
itself includes the segments array. This means a single spin response
gives the frontend everything it needs to:
  - Render the wheel before spinning (segments list)
  - Animate to the correct slot after spinning (segment_position)
  - Display the result label (segment_label)
"""
from decimal import Decimal

from rest_framework import serializers

from .models import Spin, Wheel, WheelSegment


class WheelSegmentPublicSerializer(serializers.ModelSerializer):
    """
    Public segment data for frontend wheel rendering.

    DELIBERATELY EXCLUDES probability_weight — that's a private game
    configuration that would let users compute the exact house edge.

    Industry standard: real-money gambling platforms expose game state
    (what segments exist, where they are, their payouts) but NEVER
    expose per-segment win probabilities.
    """
    multiplier = serializers.DecimalField(
        max_digits=10, decimal_places=4, coerce_to_string=True,
    )

    class Meta:
        model = WheelSegment
        fields = [
            'position',     # 0-indexed slot for animation alignment
            'label',        # user-visible text ("Loss", "3×", "₦5000")
            'multiplier',   # payout multiplier (decimal string)
            'color',        # hex color for rendering
        ]


class WheelPublicSerializer(serializers.ModelSerializer):
    """
    Public wheel data for the frontend.

    Includes the full segments array so the frontend can render the
    wheel accurately. When a spin result returns segment_position: 4,
    the frontend looks up segments[4] in this array to find the label,
    multiplier, and color to display.
    """
    min_stake = serializers.DecimalField(
        max_digits=20, decimal_places=2, coerce_to_string=True,
    )
    max_stake = serializers.DecimalField(
        max_digits=20, decimal_places=2, coerce_to_string=True,
    )
    rtp_target = serializers.DecimalField(
        max_digits=5, decimal_places=2, coerce_to_string=True,
        allow_null=True,
    )
    segments = serializers.SerializerMethodField()

    class Meta:
        model = Wheel
        fields = [
            'id',
            'wheel_type',
            'name',
            'currency_type',
            'min_stake',
            'max_stake',
            'is_welcome_only',
            'rtp_target',
            'segments',
        ]

    def get_segments(self, wheel):
        """Return active segments ordered by position."""
        active_segments = wheel.segments.filter(is_active=True).order_by('position')
        return WheelSegmentPublicSerializer(active_segments, many=True).data



class SpinRequestSerializer(serializers.Serializer):
    # wheel_id = serializers.UUIDField(required=True)
    # stake_amount = serializers.DecimalField(
    #     max_digits=15, decimal_places=2, required=True,
    # )
    # client_seed = serializers.CharField(
    #     max_length=128, required=False, allow_blank=True,
    # )
    # source_wallet = serializers.ChoiceField(
    #     choices=[
    #         ('deposit_coins', 'Deposit Coins'),
    #         ('bonus_coins', 'Bonus Coins'),
    #     ],
    #     default='deposit_coins',
    #     required=False,
    #     help_text='Which coin balance to spin from.',
    # )
    wheel_id = serializers.UUIDField(required=True)
    stake_amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, required=True,
        min_value=Decimal('0.000001'),
    )
    source_wallet = serializers.ChoiceField(
        choices=[
            ('crypto_coins', 'Crypto Coins'),
            ('naira_coins', 'Naira Coins'),
            ('bonus_coins', 'Bonus Coins'),
        ],
        default='naira_coins',
        required=False,
        help_text='Which coin balance to spin from.',
    )
    bonus_destination = serializers.ChoiceField(
        choices=[
            ('crypto', 'Crypto Withdraw Balance'),
            ('naira', 'Naira Withdraw Balance'),
        ],
        required=False,
        allow_blank=True,
        default='',
        help_text='Required when source_wallet=bonus_coins.',
    )
    client_seed = serializers.CharField(
        max_length=128, required=False, allow_blank=True, default='',
    )
 
    def validate(self, data):
        """
        Cross-field validation:
            bonus_coins source REQUIRES bonus_destination.
        """
        source = data.get('source_wallet', 'naira_coins')
        destination = data.get('bonus_destination', '')
 
        if source == 'bonus_coins' and not destination:
            raise serializers.ValidationError({
                'bonus_destination': (
                    'bonus_destination is required when spinning from bonus_coins. '
                    "Pass 'crypto' or 'naira'."
                ),
            })
 
        # For non-bonus spins, drop the destination (it's noise)
        if source != 'bonus_coins':
            data['bonus_destination'] = ''
 
        return data

class WelcomeSpinRequestSerializer(serializers.Serializer):
    """POST /api/v1/spin/welcome/"""
    client_seed = serializers.CharField(
        max_length=128, required=False, allow_blank=True, default='',
    )


# ─── Spin response serializer ──────────────────────────────────────────────

class SpinPublicSerializer(serializers.ModelSerializer):
    """
    Spin result returned to the user.

    The nested `wheel` field uses WheelPublicSerializer which now
    includes the full segments array. So a spin response is fully
    self-contained — frontend doesn't need a separate API call to
    figure out segment details.
    """
    wheel = WheelPublicSerializer(read_only=True)
    stake_amount = serializers.DecimalField(
        max_digits=20, decimal_places=2, coerce_to_string=True,
    )
    payout_amount = serializers.DecimalField(
        max_digits=20, decimal_places=2, coerce_to_string=True,
    )
    multiplier = serializers.SerializerMethodField()
    segment_label = serializers.SerializerMethodField()
    segment_position = serializers.SerializerMethodField()

    class Meta:
        model = Spin
        fields = [
            'id',
            'wheel',
            'stake_amount',
            'reference',
            'segment_label',
            'segment_position',
            'multiplier',
            'payout_amount',
            'bonus_destination',
            'payout_currency',  
            'credited_balance',  
            'source_wallet',
            'outcome',
            'server_seed_hash',
            'client_seed',
            'nonce',
            'is_welcome_spin',
            'created_at',
        ]

    def get_multiplier(self, spin):
        """Multiplier as decimal string (avoid float precision issues)."""
        return str(spin.segment_landed.multiplier)

    def get_segment_label(self, spin):
        return spin.segment_landed.label

    def get_segment_position(self, spin):
        return spin.segment_landed.position