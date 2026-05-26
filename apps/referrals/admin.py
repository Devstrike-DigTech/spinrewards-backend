"""
Django admin registration for Referrals.

Register at: apps/referrals/admin.py
"""
from django.contrib import admin
from django.utils.html import format_html

from .models import Referral, ReferralCode


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = (
        'code',
        'user_display',
        'referrals_made_count',
        'is_active',
        'created_at',
    )
    list_filter = ('is_active', 'created_at')
    search_fields = (
        'code',
        'user__telegram_id',
        'user__first_name',
        'user__last_name',
        'user__username',
    )
    readonly_fields = (
        'id',
        'code',
        'user',
        'created_at',
        'updated_at',
        'referrals_made_count',
    )
    ordering = ('-created_at',)

    @admin.display(description='User')
    def user_display(self, obj):
        u = obj.user
        parts = [u.first_name or '', u.last_name or '']
        name = ' '.join(p for p in parts if p) or u.username or f'#{u.telegram_id}'
        return f'{name} (@{u.telegram_id})'

    @admin.display(description='Referrals Made')
    def referrals_made_count(self, obj):
        return obj.referrals.count()

    def has_add_permission(self, request):
        # Codes are auto-generated, not created manually
        return False


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = (
        'referrer_display',
        'referred_display',
        'code_display',
        'status_display',
        'qualified_at',
        'rewarded_at',
        'created_at',
    )
    list_filter = ('status', 'created_at', 'qualified_at', 'rewarded_at')
    search_fields = (
        'referrer__telegram_id',
        'referrer__first_name',
        'referrer__last_name',
        'referrer__username',
        'referred_user__telegram_id',
        'referred_user__first_name',
        'referred_user__last_name',
        'referred_user__username',
        'referral_code__code',
    )
    readonly_fields = (
        'id',
        'referrer',
        'referred_user',
        'referral_code',
        'qualified_at',
        'rewarded_at',
        'reward_snapshot',
        'created_at',
        'updated_at',
    )
    fields = (
        'id',
        'referrer',
        'referred_user',
        'referral_code',
        'status',
        'rejection_reason',
        'qualified_at',
        'rewarded_at',
        'reward_snapshot',
        'created_at',
        'updated_at',
    )
    ordering = ('-created_at',)

    @admin.display(description='Referrer', ordering='referrer__telegram_id')
    def referrer_display(self, obj):
        u = obj.referrer
        parts = [u.first_name or '', u.last_name or '']
        name = ' '.join(p for p in parts if p) or u.username or f'#{u.telegram_id}'
        return f'{name} (@{u.telegram_id})'

    @admin.display(description='Referred User', ordering='referred_user__telegram_id')
    def referred_display(self, obj):
        u = obj.referred_user
        parts = [u.first_name or '', u.last_name or '']
        name = ' '.join(p for p in parts if p) or u.username or f'#{u.telegram_id}'
        return f'{name} (@{u.telegram_id})'

    @admin.display(description='Code')
    def code_display(self, obj):
        return obj.referral_code.code if obj.referral_code else '—'

    @admin.display(description='Status')
    def status_display(self, obj):
        colors = {
            'pending':   '#6a6a82',
            'qualified': '#C9961A',
            'rewarded':  '#1a8a3a',
            'rejected':  '#d32f2f',
        }
        color = colors.get(obj.status, '#6a6a82')
        return format_html(
            '<span style="color: {}; font-weight: 600;">{}</span>',
            color, obj.get_status_display(),
        )

    def has_add_permission(self, request):
        # Referrals are created by the apply flow, not manually
        return False

    actions = ['mark_as_rejected']

    @admin.action(description='Mark selected as rejected')
    def mark_as_rejected(self, request, queryset):
        n = queryset.update(status=Referral.Status.REJECTED)
        self.message_user(request, f'Marked {n} referrals as rejected.')