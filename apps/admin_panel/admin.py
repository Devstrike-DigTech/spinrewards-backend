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
from django.contrib import admin
from django.utils.html import format_html

from .models import AdminProfile


@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = (
        "email",
        "display_name",
        "user",
        "role",
        "is_active",
        "is_super_admin_display",
        "last_login_at",
        "created_at",
    )

    list_filter = (
        "role",
        "is_active",
        "created_at",
        "last_login_at",
    )

    search_fields = (
        "email",
        "display_name",
        "user__email",
        "user__username",
        "user__first_name",
        "user__last_name",
    )

    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "last_login_at",
    )

    ordering = ("-created_at",)

    fieldsets = (
        (
            "Admin Information",
            {
                "fields": (
                    "id",
                    "user",
                    "email",
                    "display_name",
                    "role",
                    "is_active",
                )
            },
        ),
        (
            "Authentication",
            {
                "fields": (
                    "hashed_password",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "last_login_at",
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )

    def is_super_admin_display(self, obj):
        if obj.is_super_admin:
            return format_html(
                '<span style="color: green; font-weight: bold;">Yes</span>'
            )
        return format_html(
            '<span style="color: red; font-weight: bold;">No</span>'
        )

    is_super_admin_display.short_description = "Super Admin"