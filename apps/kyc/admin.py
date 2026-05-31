"""Admin panel for KYC module. Lets ops review submissions."""
import json

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import BankAccount, KYCDocument, KYCProfile


@admin.register(KYCProfile)
class KYCProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'overall_status_badge', 'full_name',
        'personal_info_status', 'bank_account_status', 'document_status',
        'submitted_at',
    )
    list_filter = (
        'personal_info_status', 'bank_account_status', 'document_status',
    )
    search_fields = ('user__telegram_id', 'user__first_name', 'full_name')
    readonly_fields = (
        'id', 'created_at', 'updated_at',
        'submitted_at', 'last_resubmission_at', 'reviewed_at',
        'overall_status_display',
        'nin_lookup_response_pretty', 'bvn_lookup_response_pretty',
    )
    fieldsets = (
        ('User', {'fields': ('id', 'user', 'overall_status_display')}),
        ('Personal Information', {
            'fields': (
                'full_name', 'date_of_birth', 'phone_number',
                'personal_info_status', 'personal_info_reason',
                'nin_lookup_response_pretty', 'bvn_lookup_response_pretty',
            ),
        }),
        ('Bank Account', {
            'fields': ('bank_account_status', 'bank_account_reason'),
        }),
        ('Document', {
            'fields': ('document_status', 'document_reason'),
        }),
        ('Timestamps', {
            'fields': (
                'submitted_at', 'last_resubmission_at',
                'reviewed_at', 'created_at', 'updated_at',
            ),
        }),
    )

    def overall_status_badge(self, obj):
        colors = {
            'approved': 'green',
            'rejected': 'red',
            'partial': 'orange',
            'pending': 'gray',
            'unverified': 'lightgray',
        }
        color = colors.get(obj.overall_status, 'black')
        return format_html(
            '<span style="color: {}; font-weight: bold;">●</span> {}',
            color, obj.overall_status,
        )
    overall_status_badge.short_description = 'Status'

    def overall_status_display(self, obj):
        return obj.overall_status
    overall_status_display.short_description = 'Overall'

    def nin_lookup_response_pretty(self, obj):
        return format_html(
            '<pre>{}</pre>',
            json.dumps(obj.nin_lookup_response, indent=2, default=str),
        )
    nin_lookup_response_pretty.short_description = 'NIN Lookup'

    def bvn_lookup_response_pretty(self, obj):
        return format_html(
            '<pre>{}</pre>',
            json.dumps(obj.bvn_lookup_response, indent=2, default=str),
        )
    bvn_lookup_response_pretty.short_description = 'BVN Lookup'


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'bank_name', 'account_number_masked', 'account_name',
        'is_active', 'verified_at',
    )
    list_filter = ('is_active', 'bank_name')
    search_fields = ('user__telegram_id', 'account_number', 'account_name')
    readonly_fields = ('id', 'created_at', 'updated_at', 'verified_at')

    def account_number_masked(self, obj):
        if not obj.account_number:
            return '****'
        return f'****{obj.account_number[-4:]}'
    account_number_masked.short_description = 'Account #'


@admin.register(KYCDocument)
class KYCDocumentAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'document_type', 'original_filename',
        'file_size_kb', 'status', 'uploaded_at',
    )
    list_filter = ('document_type', 'status',)
    search_fields = ('user__telegram_id', 'original_filename')
    readonly_fields = (
        'id', 'uploaded_at', 'reviewed_at',
        'file_size_bytes', 'content_type',
    )
    actions = ['mark_verified', 'mark_rejected']

    def file_size_kb(self, obj):
        if not obj.file_size_bytes:
            return ''
        return f'{obj.file_size_bytes / 1024:.0f} KB'
    file_size_kb.short_description = 'Size'

    def mark_verified(self, request, queryset):
        queryset.update(
            status=KYCDocument.Status.VERIFIED,
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f'{queryset.count()} document(s) marked verified.')
    mark_verified.short_description = 'Mark as verified'

    def mark_rejected(self, request, queryset):
        queryset.update(
            status=KYCDocument.Status.REJECTED,
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f'{queryset.count()} document(s) marked rejected.')
    mark_rejected.short_description = 'Mark as rejected'