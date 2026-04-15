import uuid
from django.db import models


class KYC(models.Model):
    class Status(models.TextChoices):
        UNVERIFIED = 'unverified', 'Unverified'
        PENDING = 'pending', 'Pending Review'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        'users.User', on_delete=models.CASCADE, related_name='kyc'
    )
    full_name = models.CharField(max_length=200)

    # Encrypted at rest using Fernet (see common/utils.py encrypt/decrypt)
    nin_encrypted = models.TextField()

    dob = models.DateField()
    bank_account = models.CharField(max_length=20)
    bank_code = models.CharField(max_length=10)
    account_name = models.CharField(max_length=200, blank=True)

    document_url = models.TextField(blank=True)
    document_type = models.CharField(max_length=50, blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    rejection_reason = models.TextField(blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        'users.User', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='kyc_reviews',
    )

    class Meta:
        db_table = 'kyc'
        verbose_name = 'KYC'
        verbose_name_plural = 'KYC Records'

    def __str__(self):
        return f'KYC({self.user}, {self.status})'
