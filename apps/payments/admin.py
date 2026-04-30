# from django.contrib import admin
# from .models import DepositSession


# @admin.register(DepositSession)
# class DepositSessionAdmin(admin.ModelAdmin):
#     list_display = ('user', 'amount', 'method', 'status', 'created_at', 'completed_at')
#     list_filter = ('method', 'status')
#     search_fields = ('user__telegram_id', 'provider_reference')
#     readonly_fields = ('id', 'created_at', 'updated_at', 'provider_reference')
#     ordering = ('-created_at',)
from django.contrib import admin

from .models import Deposit, VirtualAccount


@admin.register(Deposit)
class DepositAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'user', 'provider', 'amount',
        'status', 'completed_at',
    )
    list_filter = ('provider', 'status')
    search_fields = (
        'user__telegram_id', 'user__username',
        'internal_reference', 'provider_reference',
    )
    readonly_fields = (
        'id', 'internal_reference', 'provider_reference',
        'wallet_transaction', 'created_at', 'updated_at',
        'webhook_received_at', 'completed_at',
    )
    ordering = ('-created_at',)
    raw_id_fields = ('user', 'wallet_transaction')


@admin.register(VirtualAccount)
class VirtualAccountAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'account_number', 'bank_name', 'is_active', 'created_at',
    )
    search_fields = (
        'user__telegram_id', 'user__username',
        'account_number', 'monnify_account_reference',
    )
    readonly_fields = (
        'id', 'monnify_account_reference', 'created_at', 'updated_at',
    )
    raw_id_fields = ('user',)