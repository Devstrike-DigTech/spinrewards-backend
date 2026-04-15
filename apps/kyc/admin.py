from django.contrib import admin
from .models import KYC


@admin.register(KYC)
class KYCAdmin(admin.ModelAdmin):
    list_display = ('user', 'full_name', 'status', 'submitted_at', 'reviewed_at')
    list_filter = ('status',)
    search_fields = ('user__telegram_id', 'full_name', 'bank_account')
    readonly_fields = ('id', 'submitted_at', 'nin_encrypted')
    ordering = ('-submitted_at',)
