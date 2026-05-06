# from django.contrib import admin
# from .models import WithdrawalRequest


# @admin.register(WithdrawalRequest)
# class WithdrawalRequestAdmin(admin.ModelAdmin):
#     list_display = ('user', 'amount', 'status', 'reference', 'created_at', 'processed_at')
#     list_filter = ('status',)
#     search_fields = ('user__telegram_id', 'reference', 'bank_account')
#     readonly_fields = ('id', 'reference', 'created_at', 'updated_at')
#     ordering = ('-created_at',)

"""Admin panel for withdrawals."""
import json

from django.contrib import admin
from django.utils.html import format_html

from .models import Withdrawal
from .services import WithdrawalService, WithdrawalServiceError


@admin.register(Withdrawal)
class WithdrawalAdmin(admin.ModelAdmin):
    list_display = (
        'reference_short', 'user', 'amount_display',
        'status_badge', 'review_display',
        'requested_at',
    )
    list_filter = ('status', 'requires_review', 'forced_manual_review', 'provider')
    search_fields = (
        'user__telegram_id', 'user__first_name',
        'reference', 'provider_transfer_id',
    )
    readonly_fields = (
        'id', 'reference',
        'requested_at', 'processing_at', 'completed_at', 'updated_at',
        'provider_recipient_id', 'provider_transfer_id',
        'provider_response_pretty',
        'debit_transaction', 'refund_transaction',
        'reviewed_by', 'reviewed_at',
        'forced_manual_review',
    )
    fieldsets = (
        ('User & Bank', {
            'fields': ('id', 'user', 'bank_account'),
        }),
        ('Amount', {
            'fields': ('amount', 'fee', 'net_amount'),
        }),
        ('Status', {
            'fields': (
                'status', 'requires_review', 'forced_manual_review',
                'failure_reason',
            ),
        }),
        ('Provider', {
            'fields': (
                'provider', 'provider_recipient_id',
                'provider_transfer_id', 'provider_response_pretty',
            ),
        }),
        ('Audit', {
            'fields': (
                'reference',
                'debit_transaction', 'refund_transaction',
                'reviewed_by', 'reviewed_at', 'review_notes',
            ),
        }),
        ('Timestamps', {
            'fields': ('requested_at', 'processing_at', 'completed_at', 'updated_at'),
        }),
    )

    actions = ['admin_approve', 'admin_reject']

    def reference_short(self, obj):
        return obj.reference[:20] + '...' if len(obj.reference) > 20 else obj.reference
    reference_short.short_description = 'Reference'

    def amount_display(self, obj):
        return f'₦{obj.amount:,.2f}'
    amount_display.short_description = 'Amount'

    def status_badge(self, obj):
        colors = {
            'completed': 'green',
            'failed': 'red',
            'rejected': 'red',
            'cancelled': 'gray',
            'processing': 'orange',
            'pending': 'blue',
            'pending_review': 'purple',
        }
        color = colors.get(obj.status, 'black')
        return format_html(
            '<span style="color: {}; font-weight: bold;">●</span> {}',
            color, obj.get_status_display(),
        )
    status_badge.short_description = 'Status'

    def review_display(self, obj):
        if obj.forced_manual_review:
            mark = '🔒 Forced'
        elif obj.requires_review:
            mark = '⚠ Required'
        else:
            mark = '—'
        if obj.reviewed_at:
            return f'{mark} ({obj.reviewed_by})'
        return mark
    review_display.short_description = 'Review'

    def provider_response_pretty(self, obj):
        return format_html(
            '<pre>{}</pre>',
            json.dumps(obj.provider_response, indent=2, default=str),
        )
    provider_response_pretty.short_description = 'Provider Response'

    def admin_approve(self, request, queryset):
        approved, skipped = 0, 0
        for w in queryset:
            try:
                WithdrawalService.approve(w, request.user, notes='Bulk approved')
                approved += 1
            except WithdrawalServiceError:
                skipped += 1
        self.message_user(
            request,
            f'Approved {approved}. Skipped {skipped} (wrong state).',
        )
    admin_approve.short_description = 'Approve selected'

    def admin_reject(self, request, queryset):
        rejected, skipped = 0, 0
        for w in queryset:
            try:
                WithdrawalService.reject(w, request.user, reason='Bulk rejected')
                rejected += 1
            except WithdrawalServiceError:
                skipped += 1
        self.message_user(
            request,
            f'Rejected {rejected}. Skipped {skipped} (wrong state).',
        )
    admin_reject.short_description = 'Reject selected'