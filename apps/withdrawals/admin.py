# from django.contrib import admin
# from .models import WithdrawalRequest


# @admin.register(WithdrawalRequest)
# class WithdrawalRequestAdmin(admin.ModelAdmin):
#     list_display = ('user', 'amount', 'status', 'reference', 'created_at', 'processed_at')
#     list_filter = ('status',)
#     search_fields = ('user__telegram_id', 'reference', 'bank_account')
#     readonly_fields = ('id', 'reference', 'created_at', 'updated_at')
#     ordering = ('-created_at',)
