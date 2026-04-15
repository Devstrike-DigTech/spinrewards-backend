from django.contrib import admin
from .models import DepositSession


@admin.register(DepositSession)
class DepositSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'amount', 'method', 'status', 'created_at', 'completed_at')
    list_filter = ('method', 'status')
    search_fields = ('user__telegram_id', 'provider_reference')
    readonly_fields = ('id', 'created_at', 'updated_at', 'provider_reference')
    ordering = ('-created_at',)
