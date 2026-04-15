from django.contrib import admin
from .models import Wallet, Transaction


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'coin_balance', 'cash_balance', 'created_at')
    readonly_fields = ('id', 'created_at')
    search_fields = ('user__telegram_id', 'user__username')


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'type', 'balance_type', 'amount', 'status', 'created_at')
    list_filter = ('type', 'balance_type', 'status')
    search_fields = ('user__telegram_id', 'reference_id')
    readonly_fields = ('id', 'created_at', 'updated_at')
    ordering = ('-created_at',)
