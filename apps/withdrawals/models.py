# import uuid
# from django.db import models


# class WithdrawalRequest(models.Model):
#     class Status(models.TextChoices):
#         PENDING = 'pending', 'Pending'
#         PROCESSING = 'processing', 'Processing'
#         COMPLETED = 'completed', 'Completed'
#         FAILED = 'failed', 'Failed'
#         REVERSED = 'reversed', 'Reversed'

#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     user = models.ForeignKey(
#         'users.User', on_delete=models.CASCADE, related_name='withdrawal_requests'
#     )
#     amount = models.DecimalField(max_digits=15, decimal_places=2)
#     bank_account = models.CharField(max_length=20)
#     bank_code = models.CharField(max_length=10)
#     bank_name = models.CharField(max_length=100, blank=True)
#     account_name = models.CharField(max_length=200, blank=True)

#     reference = models.CharField(max_length=100, unique=True, db_index=True)
#     provider_reference = models.CharField(max_length=200, blank=True)

#     status = models.CharField(
#         max_length=20, choices=Status.choices, default=Status.PENDING
#     )
#     failure_reason = models.TextField(blank=True)

#     transaction = models.OneToOneField(
#         'wallet.Transaction', on_delete=models.SET_NULL,
#         null=True, blank=True, related_name='withdrawal',
#     )

#     created_at = models.DateTimeField(auto_now_add=True)
#     processed_at = models.DateTimeField(null=True, blank=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         db_table = 'withdrawal_requests'
#         ordering = ['-created_at']

#     def __str__(self):
#         return f'Withdrawal({self.user}, ₦{self.amount}, {self.status})'
