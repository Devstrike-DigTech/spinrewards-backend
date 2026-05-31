# """
# KYC module models.

# Three models:
#     KYCProfile      One per user. Holds typed personal info + per-section
#                     statuses + cached provider responses.
#     BankAccount     One ACTIVE per user. Replaceable — old account is
#                     deactivated (not deleted) when a new one verifies.
#     KYCDocument     File uploads (utility bill or bank statement).
#                     Tier 1 storage only for v1.

# The KYCProfile has three INDEPENDENT section statuses:
#     - personal_info_status   (NIN + BVN + name match + DOB consistency)
#     - bank_account_status    (account name resolved + matches identity)
#     - document_status        (file uploaded and valid)

# Overall status is computed from these three. Approved only when all three
# are 'verified'. User can fix one failed section and resubmit without
# losing verified state on the others.

# PII fields (NIN, BVN) are encrypted at rest using Fernet (AES-128-CBC +
# HMAC-SHA256). Encryption key loaded from settings.ENCRYPTION_KEY.
# """
# import uuid

# from cryptography.fernet import Fernet, InvalidToken
# from django.conf import settings
# from django.core.exceptions import ValidationError
# from django.db import models
# from django.db.models import Q


# # ─── Encryption helper ─────────────────────────────────────────────────────

# def _fernet() -> Fernet:
#     """Get a Fernet instance for encrypting PII at rest."""
#     key = settings.ENCRYPTION_KEY
#     if not key:
#         raise ValidationError('ENCRYPTION_KEY is not configured.')
#     if isinstance(key, str):
#         key = key.encode()
#     return Fernet(key)


# class EncryptedField(models.TextField):
#     """
#     A TextField that transparently encrypts/decrypts on write/read.

#     - Encrypted values stored as URL-safe base64 strings
#     - Reading a value that wasn't encrypted (e.g., legacy data) returns
#       it verbatim, so this is safe to introduce gradually
#     """

#     description = 'Encrypted text field'

#     def from_db_value(self, value, expression, connection):
#         if value is None or value == '':
#             return value
#         try:
#             return _fernet().decrypt(value.encode()).decode()
#         except (InvalidToken, ValueError):
#             return value  # not encrypted (legacy)

#     def to_python(self, value):
#         if value is None:
#             return value
#         if isinstance(value, str):
#             try:
#                 return _fernet().decrypt(value.encode()).decode()
#             except (InvalidToken, ValueError):
#                 return value
#         return value

#     def get_prep_value(self, value):
#         if value is None or value == '':
#             return value
#         return _fernet().encrypt(str(value).encode()).decode()


# # ─── KYCProfile ────────────────────────────────────────────────────────────

# class KYCProfile(models.Model):
#     """KYC verification record for a user. One per user."""

#     class SectionStatus(models.TextChoices):
#         PENDING = 'pending', 'Pending'
#         VERIFIED = 'verified', 'Verified'
#         REJECTED = 'rejected', 'Rejected'
#         REQUIRES_CORRECTION = 'requires_correction', 'Requires Correction'

#     class OverallStatus(models.TextChoices):
#         UNVERIFIED = 'unverified', 'Unverified'
#         PENDING = 'pending', 'Pending Review'
#         PARTIAL = 'partial', 'Partially Verified'
#         APPROVED = 'approved', 'Approved'
#         REJECTED = 'rejected', 'Rejected'

#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     user = models.OneToOneField(
#         'users.User', on_delete=models.CASCADE, related_name='kyc_profile',
#     )

#     # ── User-supplied personal info ──
#     full_name = models.CharField(max_length=200)
#     nin = EncryptedField(blank=True)
#     bvn = EncryptedField(blank=True)
#     date_of_birth = models.DateField(null=True, blank=True)
#     phone_number = models.CharField(max_length=20, blank=True)

#     # ── Per-section statuses ──
#     personal_info_status = models.CharField(
#         max_length=25, choices=SectionStatus.choices,
#         default=SectionStatus.PENDING,
#     )
#     bank_account_status = models.CharField(
#         max_length=25, choices=SectionStatus.choices,
#         default=SectionStatus.PENDING,
#     )
#     document_status = models.CharField(
#         max_length=25, choices=SectionStatus.choices,
#         default=SectionStatus.PENDING,
#     )

#     # ── Per-section failure reasons ──
#     personal_info_reason = models.CharField(max_length=500, blank=True)
#     bank_account_reason = models.CharField(max_length=500, blank=True)
#     document_reason = models.CharField(max_length=500, blank=True)

#     # ── Cached provider responses (audit trail) ──
#     nin_lookup_response = models.JSONField(default=dict, blank=True)
#     bvn_lookup_response = models.JSONField(default=dict, blank=True)
#     provider_reference = models.CharField(max_length=200, blank=True)

#     # ── Timestamps ──
#     submitted_at = models.DateTimeField(null=True, blank=True)
#     last_resubmission_at = models.DateTimeField(null=True, blank=True)
#     reviewed_at = models.DateTimeField(null=True, blank=True)
#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         db_table = 'kyc_profiles'
#         ordering = ['-created_at']

#     def __str__(self):
#         return f'KYC({self.user.telegram_id}, {self.overall_status})'

#     @property
#     def overall_status(self) -> str:
#         """
#         Computed from the three section statuses.

#         Returns one of OverallStatus values:
#           UNVERIFIED  — all sections still pending (just submitted)
#           PENDING     — some sections verified, some still pending
#           APPROVED    — all three sections verified
#           PARTIAL     — at least one needs correction
#           REJECTED    — at least one outright rejected
#         """
#         statuses = [
#             self.personal_info_status,
#             self.bank_account_status,
#             self.document_status,
#         ]

#         if all(s == self.SectionStatus.VERIFIED for s in statuses):
#             return self.OverallStatus.APPROVED

#         if all(s == self.SectionStatus.PENDING for s in statuses):
#             return self.OverallStatus.UNVERIFIED

#         if any(s == self.SectionStatus.REJECTED for s in statuses):
#             return self.OverallStatus.REJECTED

#         if any(s == self.SectionStatus.REQUIRES_CORRECTION for s in statuses):
#             return self.OverallStatus.PARTIAL

#         return self.OverallStatus.PENDING

#     @property
#     def can_withdraw(self) -> bool:
#         """Withdrawals require fully approved KYC."""
#         return self.overall_status == self.OverallStatus.APPROVED


# # ─── BankAccount ───────────────────────────────────────────────────────────

# class BankAccount(models.Model):
#     """
#     User's bank account for withdrawals.

#     One active per user enforced via partial unique constraint.
#     Replaceable: when the user adds a new account, the old one is set
#     to is_active=False (kept for audit/withdrawal history).
#     """

#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     user = models.ForeignKey(
#         'users.User', on_delete=models.CASCADE, related_name='bank_accounts',
#     )

#     bank_code = models.CharField(max_length=10)
#     bank_name = models.CharField(max_length=100)
#     account_number = models.CharField(max_length=20)
#     account_name = models.CharField(max_length=200)

#     is_active = models.BooleanField(default=True)
#     verified_at = models.DateTimeField(null=True, blank=True)

#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     class Meta:
#         db_table = 'bank_accounts'
#         ordering = ['-is_active', '-created_at']
#         constraints = [
#             models.UniqueConstraint(
#                 fields=['user'],
#                 condition=Q(is_active=True),
#                 name='one_active_bank_account_per_user',
#             ),
#         ]

#     def __str__(self):
#         return (
#             f'BankAccount({self.user.telegram_id}, '
#             f'{self.bank_name}, ***{self.account_number[-4:]})'
#         )


# # ─── KYCDocument ───────────────────────────────────────────────────────────

# def kyc_document_upload_path(instance, filename):
#     """Organize uploads by user UUID."""
#     return f'kyc_documents/{instance.user.id}/{filename}'


# class KYCDocument(models.Model):
#     """
#     A document uploaded by the user as part of KYC.

#     Tier 1 (v1): storage only. Files marked verified on upload after
#     file-type/size validation. Admin can review and reject if doc is
#     clearly fake.

#     Tier 2 (future): pass through Dojah OCR for automated address
#     verification on utility bills.
#     """

#     class DocumentType(models.TextChoices):
#         UTILITY_BILL = 'utility_bill', 'Utility Bill'
#         BANK_STATEMENT = 'bank_statement', 'Bank Statement'

#     class Status(models.TextChoices):
#         UPLOADED = 'uploaded', 'Uploaded'
#         VERIFIED = 'verified', 'Verified'
#         REJECTED = 'rejected', 'Rejected'

#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     user = models.ForeignKey(
#         'users.User', on_delete=models.CASCADE, related_name='kyc_documents',
#     )

#     file = models.FileField(upload_to=kyc_document_upload_path)
#     original_filename = models.CharField(max_length=255)
#     document_type = models.CharField(
#         max_length=20, choices=DocumentType.choices,
#         default=DocumentType.UTILITY_BILL,
#     )
#     file_size_bytes = models.BigIntegerField(default=0)
#     content_type = models.CharField(max_length=100, blank=True)

#     status = models.CharField(
#         max_length=20, choices=Status.choices,
#         default=Status.UPLOADED,
#     )
#     rejection_reason = models.CharField(max_length=500, blank=True)

#     is_active = models.BooleanField(
#         default=True,
#         help_text='False if superseded by a newer upload.',
#     )

#     uploaded_at = models.DateTimeField(auto_now_add=True)
#     reviewed_at = models.DateTimeField(null=True, blank=True)

#     class Meta:
#         db_table = 'kyc_documents'
#         ordering = ['-uploaded_at']

#     def __str__(self):
#         return f'KYCDocument({self.user.telegram_id}, {self.document_type}, {self.status})'
"""
KYC module models — UPDATED for NIN-only flow.

Three models:
    KYCProfile      One per user. NIN identity check + optional document upload.
                    BVN fields are deprecated but kept for backward compatibility.
    BankAccount     Multiple per user. Saved after first successful withdrawal.
                    Lifecycle is now managed by the withdrawals app.
    KYCDocument     File uploads (utility bill or bank statement). Unchanged.

The KYCProfile has TWO active section statuses post-redesign:
    - personal_info_status   (NIN + name match — BVN no longer required)
    - document_status        (file uploaded and valid)

The bank_account_status field remains in the DB but is auto-set to VERIFIED
once NIN passes (it's no longer a real check at KYC time — bank verification
moved to withdrawal time).

PII fields (NIN) are encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256).
"""
import uuid

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


# ─── Encryption helper ─────────────────────────────────────────────────────
def kyc_document_upload_path(instance, filename):
    """Upload path for KYC documents. Referenced by migration 0003."""
    return f'kyc/documents/{instance.user.id}/{filename}'

def _fernet() -> Fernet:
    """Get a Fernet instance for encrypting PII at rest."""
    key = settings.ENCRYPTION_KEY
    if not key:
        raise ValidationError('ENCRYPTION_KEY is not configured.')
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


class EncryptedField(models.TextField):
    """A TextField that transparently encrypts/decrypts on write/read."""

    description = 'Encrypted text field'

    def from_db_value(self, value, expression, connection):
        if value is None or value == '':
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            return value

    def to_python(self, value):
        if value is None:
            return value
        if isinstance(value, str):
            try:
                return _fernet().decrypt(value.encode()).decode()
            except (InvalidToken, ValueError):
                return value
        return value

    def get_prep_value(self, value):
        if value is None or value == '':
            return value
        return _fernet().encrypt(str(value).encode()).decode()


# ─── KYCProfile ────────────────────────────────────────────────────────────

class KYCProfile(models.Model):
    """KYC verification record for a user. One per user."""

    class SectionStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        VERIFIED = 'verified', 'Verified'
        REJECTED = 'rejected', 'Rejected'
        REQUIRES_CORRECTION = 'requires_correction', 'Requires Correction'

    class OverallStatus(models.TextChoices):
        UNVERIFIED = 'unverified', 'Unverified'
        PENDING = 'pending', 'Pending Review'
        PARTIAL = 'partial', 'Partially Verified'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        'users.User', on_delete=models.CASCADE, related_name='kyc_profile',
    )

    # ── User-supplied personal info ──
    full_name = models.CharField(max_length=200)
    nin = EncryptedField(blank=True)
    bvn = EncryptedField(blank=True)   # DEPRECATED — kept for legacy data, not collected
    date_of_birth = models.DateField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)

    # ── Name returned by NIN provider (post-redesign). Plaintext — used at withdrawal time. ──
    nin_full_name = models.CharField(
        max_length=200, blank=True,
        help_text='Full name as returned by the NIN verification provider.',
    )

    # ── Per-section statuses ──
    personal_info_status = models.CharField(
        max_length=25, choices=SectionStatus.choices,
        default=SectionStatus.PENDING,
    )
    bank_account_status = models.CharField(
        max_length=25, choices=SectionStatus.choices,
        default=SectionStatus.PENDING,
        help_text='DEPRECATED — bank verification moved to withdrawal time.',
    )
    document_status = models.CharField(
        max_length=25, choices=SectionStatus.choices,
        default=SectionStatus.PENDING,
    )

    # ── Per-section failure reasons ──
    personal_info_reason = models.CharField(max_length=500, blank=True)
    bank_account_reason = models.CharField(max_length=500, blank=True)   # DEPRECATED
    document_reason = models.CharField(max_length=500, blank=True)

    # ── Cached provider responses (audit trail) ──
    nin_lookup_response = models.JSONField(default=dict, blank=True)
    bvn_lookup_response = models.JSONField(default=dict, blank=True)     # DEPRECATED
    provider_reference = models.CharField(max_length=200, blank=True)

    # ── Timestamps ──
    submitted_at = models.DateTimeField(null=True, blank=True)
    last_resubmission_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'kyc_profiles'
        ordering = ['-created_at']

    def __str__(self):
        return f'KYC({self.user.telegram_id}, {self.overall_status})'

    @property
    def nin_verified(self) -> bool:
        """True if the user's NIN has been successfully verified."""
        return self.personal_info_status == self.SectionStatus.VERIFIED

    @property
    def overall_status(self) -> str:
        """
        Post-redesign: overall status is based on NIN + document.
        Bank account status is no longer part of KYC.
        """
        relevant = [self.personal_info_status, self.document_status]

        if all(s == self.SectionStatus.VERIFIED for s in relevant):
            return self.OverallStatus.APPROVED
        if all(s == self.SectionStatus.PENDING for s in relevant):
            return self.OverallStatus.UNVERIFIED
        if any(s == self.SectionStatus.REJECTED for s in relevant):
            return self.OverallStatus.REJECTED
        if any(s == self.SectionStatus.REQUIRES_CORRECTION for s in relevant):
            return self.OverallStatus.PARTIAL
        return self.OverallStatus.PENDING

    @property
    def can_withdraw(self) -> bool:
        """
        Withdrawal eligibility — NIN must be verified.
        Document is optional unless explicitly required by policy.
        """
        return self.nin_verified


# ─── BankAccount ───────────────────────────────────────────────────────────

class BankAccount(models.Model):
    """
    Saved bank account for withdrawals.

    Created (and name-verified) by the withdrawals app at withdrawal time
    or via the saved-accounts management endpoint.

    A user can have multiple saved accounts. The most recently used one
    is `is_default=True`. `is_active=False` is a soft delete — preserves
    withdrawal history that references this account.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User', on_delete=models.CASCADE, related_name='bank_accounts',
    )
    bank_code = models.CharField(max_length=10)
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=20)
    account_name = models.CharField(
        max_length=200,
        help_text='Name returned by the bank account resolver — matched against NIN name.',
    )

    is_default = models.BooleanField(
        default=False,
        help_text='Most recently used account. Auto-managed.',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Soft delete flag. False = removed by user.',
    )
    verified_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the bank name was successfully matched against NIN.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'bank_accounts'
        ordering = ['-is_default', '-created_at']
        constraints = [
            # Only one active account per (user, bank_code, account_number)
            models.UniqueConstraint(
                fields=['user', 'bank_code', 'account_number'],
                condition=Q(is_active=True),
                name='unique_active_bank_account',
            ),
        ]

    def __str__(self):
        return f'BankAccount({self.user.telegram_id}, {self.bank_name} {self.account_number})'


# ─── KYCDocument ───────────────────────────────────────────────────────────

class KYCDocument(models.Model):
    """Uploaded ID document (utility bill, bank statement, etc.)."""

    class DocumentType(models.TextChoices):
        UTILITY_BILL = 'utility_bill', 'Utility Bill'
        BANK_STATEMENT = 'bank_statement', 'Bank Statement'
        OTHER = 'other', 'Other'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending Review'
        VERIFIED = 'verified', 'Verified'
        REJECTED = 'rejected', 'Rejected'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User', on_delete=models.CASCADE, related_name='kyc_documents',
    )
    document_type = models.CharField(
        max_length=20, choices=DocumentType.choices,
        default=DocumentType.UTILITY_BILL,
    )
    file = models.FileField(upload_to='kyc/documents/%Y/%m/')
    original_filename = models.CharField(max_length=255)
    file_size_bytes = models.PositiveIntegerField()
    content_type = models.CharField(max_length=100)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    rejection_reason = models.CharField(max_length=500, blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'kyc_documents'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'KYCDocument({self.user.telegram_id}, {self.document_type})'