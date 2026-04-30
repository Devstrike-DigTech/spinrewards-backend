# import uuid
# from django.db import models


# class AdminAuditLog(models.Model):
#     """
#     Immutable record of every admin action.
#     Never update or delete these records.
#     """
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     admin = models.ForeignKey(
#         'users.User', on_delete=models.SET_NULL, null=True,
#         related_name='audit_logs',
#     )
#     action = models.CharField(
#         max_length=100,
#         help_text='e.g. rtp_tier_updated, kyc_approved, user_suspended',
#     )
#     target_model = models.CharField(max_length=100, blank=True)
#     target_id = models.CharField(max_length=100, blank=True)
#     previous_state = models.JSONField(null=True, blank=True)
#     new_state = models.JSONField(null=True, blank=True)
#     ip_address = models.GenericIPAddressField(null=True, blank=True)
#     created_at = models.DateTimeField(auto_now_add=True)

#     class Meta:
#         db_table = 'admin_audit_logs'
#         ordering = ['-created_at']

#     def __str__(self):
#         return f'AuditLog({self.admin}, {self.action}, {self.created_at})'

#     def save(self, *args, **kwargs):
#         # Prevent updates — audit logs are immutable
#         if self.pk and AdminAuditLog.objects.filter(pk=self.pk).exists():
#             raise PermissionError('Audit logs are immutable and cannot be updated.')
#         super().save(*args, **kwargs)

#     def delete(self, *args, **kwargs):
#         raise PermissionError('Audit logs cannot be deleted.')
