# from django.contrib import admin
# from .models import AdminAuditLog


# @admin.register(AdminAuditLog)
# class AdminAuditLogAdmin(admin.ModelAdmin):
#     list_display = ('admin', 'action', 'target_model', 'target_id', 'created_at')
#     list_filter = ('action', 'target_model')
#     search_fields = ('admin__telegram_id', 'action', 'target_id')
#     readonly_fields = ('id', 'admin', 'action', 'target_model', 'target_id',
#                        'previous_state', 'new_state', 'ip_address', 'created_at')
#     ordering = ('-created_at',)

#     def has_add_permission(self, request):
#         return False

#     def has_change_permission(self, request, obj=None):
#         return False

#     def has_delete_permission(self, request, obj=None):
#         return False
