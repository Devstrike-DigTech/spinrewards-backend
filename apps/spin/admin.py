# from django.contrib import admin
# from .models import RTPTier, RTPOutcome, SpinResult


# class RTPOutcomeInline(admin.TabularInline):
#     model = RTPOutcome
#     extra = 1
#     fields = ('label', 'multiplier', 'probability', 'is_active')


# @admin.register(RTPTier)
# class RTPTierAdmin(admin.ModelAdmin):
#     list_display = ('name', 'stake_min', 'stake_max', 'rtp_target', 'computed_rtp', 'is_active')
#     list_filter = ('is_active',)
#     readonly_fields = ('id', 'created_at', 'updated_at', 'computed_rtp')
#     inlines = [RTPOutcomeInline]

#     def computed_rtp(self, obj):
#         return f'{obj.computed_rtp:.2f}%'
#     computed_rtp.short_description = 'Computed RTP'


# @admin.register(SpinResult)
# class SpinResultAdmin(admin.ModelAdmin):
#     list_display = ('user', 'stake', 'multiplier', 'win_amount', 'outcome_label', 'created_at')
#     list_filter = ('outcome_label',)
#     search_fields = ('user__telegram_id', 'idempotency_key')
#     readonly_fields = ('id', 'created_at', 'idempotency_key', 'rng_value')
#     ordering = ('-created_at',)
"""
Admin panel for the spin module.

Wheels are editable inline with their segments. The admin can adjust
multipliers and probability weights on the fly. Computed RTP is shown
at the wheel level so the admin sees what the math actually targets.
"""
from django.contrib import admin

from .models import Spin, Wheel, WheelSegment


class WheelSegmentInline(admin.TabularInline):
    model = WheelSegment
    extra = 1
    fields = ('position', 'label', 'multiplier', 'probability_weight', 'color', 'is_active')
    ordering = ('position',)


@admin.register(Wheel)
class WheelAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'wheel_type', 'currency_type',
        'min_stake', 'max_stake',
        'computed_rtp_display', 'is_active',
    )
    list_filter = ('wheel_type', 'currency_type', 'is_active')
    readonly_fields = ('id', 'created_at', 'updated_at', 'computed_rtp_display')
    inlines = [WheelSegmentInline]
    fieldsets = (
        (None, {
            'fields': ('id', 'wheel_type', 'name'),
        }),
        ('Configuration', {
            'fields': (
                'currency_type',
                'min_stake', 'max_stake',
                'is_welcome_only',
                'rtp_target', 'computed_rtp_display',
                'is_active',
            ),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def computed_rtp_display(self, obj):
        return f'{obj.computed_rtp}%'
    computed_rtp_display.short_description = 'Computed RTP'


@admin.register(Spin)
class SpinAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'user', 'wheel', 'stake_amount',
        'outcome', 'payout_amount',
    )
    list_filter = ('outcome', 'wheel', 'is_welcome_spin')
    search_fields = ('user__telegram_id', 'user__username', 'reference')
    readonly_fields = (
        'id', 'reference',
        'server_seed', 'server_seed_hash', 'client_seed', 'nonce', 'rng_value',
        'lock_transaction', 'resolution_transaction',
        'created_at',
    )
    raw_id_fields = ('user', 'wheel', 'segment_landed',
                     'lock_transaction', 'resolution_transaction')
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        # Spins must NEVER be created from admin — only via the SpinEngine
        # service, which performs proper wallet locking and atomic resolution.
        return False