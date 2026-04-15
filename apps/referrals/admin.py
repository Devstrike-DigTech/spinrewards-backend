from django.contrib import admin
from .models import Referral


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ('referrer', 'referred_user', 'status', 'referrer_bonus_amount', 'created_at')
    list_filter = ('status',)
    search_fields = ('referrer__telegram_id', 'referred_user__telegram_id')
    readonly_fields = ('id', 'created_at', 'converted_at')
