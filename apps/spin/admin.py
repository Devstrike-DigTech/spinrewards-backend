from django.contrib import admin
from .models import RTPTier, RTPOutcome, SpinResult


class RTPOutcomeInline(admin.TabularInline):
    model = RTPOutcome
    extra = 1
    fields = ('label', 'multiplier', 'probability', 'is_active')


@admin.register(RTPTier)
class RTPTierAdmin(admin.ModelAdmin):
    list_display = ('name', 'stake_min', 'stake_max', 'rtp_target', 'computed_rtp', 'is_active')
    list_filter = ('is_active',)
    readonly_fields = ('id', 'created_at', 'updated_at', 'computed_rtp')
    inlines = [RTPOutcomeInline]

    def computed_rtp(self, obj):
        return f'{obj.computed_rtp:.2f}%'
    computed_rtp.short_description = 'Computed RTP'


@admin.register(SpinResult)
class SpinResultAdmin(admin.ModelAdmin):
    list_display = ('user', 'stake', 'multiplier', 'win_amount', 'outcome_label', 'created_at')
    list_filter = ('outcome_label',)
    search_fields = ('user__telegram_id', 'idempotency_key')
    readonly_fields = ('id', 'created_at', 'idempotency_key', 'rng_value')
    ordering = ('-created_at',)
