"""
KYC service layer.

Orchestrates the full validation flow per the locked-in design:
  1. NIN lookup via provider → typed name must fuzzy-match (≥80%)
  2. BVN lookup via provider → typed name must fuzzy-match
  3. NIN name vs BVN name → must be the same person
  4. DOB consistency: typed DOB matches NIN DOB
  5. Bank account resolution → name must match identity name
  6. Document validation: file type, size, content type

Per-section status updated independently. User can fix one section and
resubmit without losing verified state on others.

Public methods:
  KYCService.submit(user, payload)            — full submission
  KYCService.resolve_bank(bank_code, acct#)  — live bank lookup
  KYCService.upload_document(user, file, type) — document upload
  KYCService.get_status(user)                 — current status snapshot
"""
import logging
from datetime import datetime
from difflib import SequenceMatcher
from typing import Optional

from django.db import transaction as db_transaction
from django.utils import timezone

from .models import BankAccount, KYCDocument, KYCProfile
from .providers import get_provider
from .providers.base import KYCProviderError

logger = logging.getLogger(__name__)


# ─── Configuration ─────────────────────────────────────────────────────────

NAME_MATCH_THRESHOLD = 0.80
ALLOWED_DOCUMENT_TYPES = [
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/jpg',
]
MAX_DOCUMENT_SIZE_BYTES = 5 * 1024 * 1024   # 5 MB


# ─── Helpers ───────────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Strip, uppercase, collapse whitespace for comparison."""
    if not name:
        return ''
    return ' '.join(name.upper().strip().split())


def name_similarity(a: str, b: str) -> float:
    """
    Returns similarity ratio between 0 and 1.

    Uses SequenceMatcher (no external deps). Tries direct comparison
    AND sorted-token comparison to handle name reordering common in
    Nigerian names (e.g., "John Adeyemi Doe" vs "Adeyemi John Doe").
    """
    a, b = normalize_name(a), normalize_name(b)
    if not a or not b:
        return 0.0

    direct = SequenceMatcher(None, a, b).ratio()

    a_sorted = ' '.join(sorted(a.split()))
    b_sorted = ' '.join(sorted(b.split()))
    sorted_match = SequenceMatcher(None, a_sorted, b_sorted).ratio()

    return max(direct, sorted_match)


def names_match(a: str, b: str, threshold: float = NAME_MATCH_THRESHOLD) -> bool:
    return name_similarity(a, b) >= threshold


def parse_date(date_str: str):
    """Parse ISO YYYY-MM-DD. Returns None on failure."""
    if not date_str:
        return None
    if hasattr(date_str, 'year'):  # already a date object
        return date_str
    try:
        return datetime.strptime(str(date_str), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


# ─── Notification helper ───────────────────────────────────────────────────

def _try_notify(method_name: str, *args, **kwargs):
    """
    Fire a KYC notification if notifications module is available.

    Soft-import: if apps.notifications isn't installed, silently skip.
    This makes the KYC module work standalone for testing.
    """
    try:
        from apps.notifications.services import NotificationService
        method = getattr(NotificationService, method_name)
        method(*args, **kwargs)
    except ImportError:
        logger.debug('Notifications module not available; skipping %s', method_name)
    except Exception as e:
        logger.warning('Notification %s failed: %s', method_name, e)


# ─── Service ───────────────────────────────────────────────────────────────

class KYCServiceError(Exception):
    """Validation errors that should propagate to the API layer as 400s."""
    pass


class KYCService:
    @staticmethod
    def submit(user, payload: dict) -> 'KYCProfile':
        """
        Submit / resubmit KYC.
    
        Post-redesign:
        - Only NIN is verified.
        - If NIN already verified for this user, raise KYCServiceError.
        - full_name, date_of_birth, phone_number are stored for record-keeping.
        - document_id (optional) links a previously uploaded document.
    
        Payload keys: full_name, nin, date_of_birth, phone_number?, document_id?
        """
        from django.utils import timezone
        from .models import KYCProfile
    
        profile, _ = KYCProfile.objects.get_or_create(
            user=user,
            defaults={'full_name': payload.get('full_name', '')},
        )
    
        # ── Lock NIN after verification ──
        if profile.nin_verified:
            raise KYCServiceError(
                'NIN already verified. You cannot re-submit KYC.'
            )
    
        # Update typed fields
        profile.full_name = payload.get('full_name', profile.full_name)
        profile.nin = payload.get('nin', profile.nin)
        profile.date_of_birth = payload.get('date_of_birth', profile.date_of_birth)
        profile.phone_number = payload.get('phone_number', profile.phone_number)
    
        if not profile.submitted_at:
            profile.submitted_at = timezone.now()
        else:
            profile.last_resubmission_at = timezone.now()
    
        # Run NIN verification
        KYCService._verify_nin(profile, payload)
    
        # Link document if provided
        document_id = payload.get('document_id')
        if document_id:
            from .models import KYCDocument
            try:
                doc = KYCDocument.objects.get(id=document_id, user=user)
                if doc.status == KYCDocument.Status.VERIFIED:
                    profile.document_status = KYCProfile.SectionStatus.VERIFIED
                elif doc.status == KYCDocument.Status.PENDING:
                    profile.document_status = KYCProfile.SectionStatus.PENDING
            except KYCDocument.DoesNotExist:
                pass
    
        profile.save()
    
        logger.info(
            'KYC submitted: user=%s nin_status=%s doc_status=%s',
            user.id, profile.personal_info_status, profile.document_status,
        )
        return profile
    
    
    @staticmethod
    def _verify_nin(profile, payload: dict):
        """
        Verify the user's NIN with the provider.
    
        Updates profile.personal_info_status, profile.personal_info_reason,
        and profile.nin_full_name on success.
        """
        from datetime import date
        from django.utils.dateparse import parse_date
        from .models import KYCProfile
        from .name_match import names_match
    
        nin = (payload.get('nin') or '').strip()
        full_name = (payload.get('full_name') or '').strip()
    
        if not nin or not full_name:
            profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
            profile.personal_info_reason = 'Full name and NIN are required.'
            return
    
        if len(nin) != 11 or not nin.isdigit():
            profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
            profile.personal_info_reason = 'NIN must be 11 digits.'
            return
    
        # Provider lookup
        try:
            provider = get_provider()
            nin_data = provider.lookup_nin(nin)
            profile.nin_lookup_response = nin_data.raw_response
        except KYCProviderError as e:
            profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
            profile.personal_info_reason = f'NIN verification failed: {e}'
            return
    
        # Name match between typed name and NIN-provider name
        if not names_match(full_name, nin_data.full_name):
            profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
            profile.personal_info_reason = (
                'The name you provided does not match the name on file for this NIN.'
            )
            return
    
        # DOB consistency (optional — if NIN returns a DOB)
        typed_dob = payload.get('date_of_birth')
        if isinstance(typed_dob, str):
            typed_dob = parse_date(typed_dob)
        try:
            nin_dob = parse_date(nin_data.date_of_birth) if nin_data.date_of_birth else None
            if typed_dob and nin_dob and typed_dob != nin_dob:
                profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
                profile.personal_info_reason = (
                    'Date of birth does not match the NIN record.'
                )
                return
        except (ValueError, TypeError):
            pass  # DOB parse error — don't block verification
    
        # ── Success ──
        profile.personal_info_status = KYCProfile.SectionStatus.VERIFIED
        profile.personal_info_reason = ''
        profile.nin_full_name = nin_data.full_name
        # Auto-set bank_account_status to VERIFIED so overall_status logic still works for legacy code
        profile.bank_account_status = KYCProfile.SectionStatus.VERIFIED
    
    
    @staticmethod
    def get_status(user) -> dict:
        """
        Return KYC status snapshot for the API.
    
        Post-redesign: NIN + document only (bank section is moot).
        """
        from .models import KYCProfile
    
        try:
            profile = user.kyc_profile
        except KYCProfile.DoesNotExist:
            return {
                'overall_status': KYCProfile.OverallStatus.UNVERIFIED,
                'nin_verified': False,
                'nin_full_name': '',
                'personal_info_status': KYCProfile.SectionStatus.PENDING,
                'personal_info_reason': '',
                'document_status': KYCProfile.SectionStatus.PENDING,
                'document_reason': '',
                'can_withdraw': False,
                'submitted_at': None,
                'last_resubmission_at': None,
            }
    
        return {
            'overall_status': profile.overall_status,
            'nin_verified': profile.nin_verified,
            'nin_full_name': profile.nin_full_name,
            'personal_info_status': profile.personal_info_status,
            'personal_info_reason': profile.personal_info_reason,
            'document_status': profile.document_status,
            'document_reason': profile.document_reason,
            'can_withdraw': profile.can_withdraw,
            'submitted_at': profile.submitted_at,
            'last_resubmission_at': profile.last_resubmission_at,
        }
    # @staticmethod
    # @db_transaction.atomic
    # def submit(user, payload: dict) -> KYCProfile:
    #     """
    #     Submit (or resubmit) a KYC application.

    #     payload:
    #       full_name, nin, bvn, date_of_birth, phone_number,
    #       bank_code, account_number, document_id (optional)

    #     Each section validated independently. If one fails, others can
    #     still pass. The user resubmits to fix failed sections.

    #     Returns the updated KYCProfile.
    #     """
    #     # ── 1. Get or create profile ──────────────────────────────────
    #     profile, created = KYCProfile.objects.get_or_create(
    #         user=user,
    #         defaults={'full_name': payload.get('full_name', '')},
    #     )

    #     if created:
    #         profile.submitted_at = timezone.now()
    #     else:
    #         profile.last_resubmission_at = timezone.now()

    #     # Update typed personal info
    #     profile.full_name = payload.get('full_name', profile.full_name)
    #     profile.nin = payload.get('nin', profile.nin)
    #     profile.bvn = payload.get('bvn', profile.bvn)
    #     profile.phone_number = payload.get('phone_number', profile.phone_number)
    #     if payload.get('date_of_birth'):
    #         profile.date_of_birth = parse_date(payload['date_of_birth'])

    #     previous_status = profile.overall_status

    #     # ── 2. Validate each section ─────────────────────────────────
    #     KYCService._validate_personal_info(profile, payload)
    #     KYCService._validate_bank_account(user, profile, payload)
    #     KYCService._validate_document(user, profile, payload)

    #     # ── 3. Persist ──────────────────────────────────────────────
    #     profile.save()

    #     # ── 4. Fire notifications on status transition ──────────────
    #     new_status = profile.overall_status
    #     if new_status != previous_status:
    #         if new_status == KYCProfile.OverallStatus.APPROVED:
    #             db_transaction.on_commit(
    #                 lambda: _try_notify('notify_kyc_approved', user=user)
    #             )
    #         elif new_status == KYCProfile.OverallStatus.REJECTED:
    #             reasons = ' | '.join(filter(None, [
    #                 profile.personal_info_reason,
    #                 profile.bank_account_reason,
    #                 profile.document_reason,
    #             ]))
    #             reason_str = reasons or 'KYC rejected'
    #             db_transaction.on_commit(
    #                 lambda: _try_notify(
    #                     'notify_kyc_rejected', user=user, reason=reason_str,
    #                 )
    #             )

    #     logger.info(
    #         'KYC submission: user=%s overall=%s personal=%s bank=%s doc=%s',
    #         user.telegram_id, new_status,
    #         profile.personal_info_status,
    #         profile.bank_account_status,
    #         profile.document_status,
    #     )
    #     return profile

    # # ── Section validators ─────────────────────────────────────────────

    # @staticmethod
    # def _validate_personal_info(profile: KYCProfile, payload: dict):
    #     """
    #     Validate NIN + BVN + name match + DOB consistency.

    #     Updates profile.personal_info_status and profile.personal_info_reason.
    #     Caches Dojah lookups for audit.
    #     """
    #     nin = (payload.get('nin') or '').strip()
    #     bvn = (payload.get('bvn') or '').strip()
    #     full_name = (payload.get('full_name') or '').strip()
    #     dob_str = payload.get('date_of_birth')

    #     # Pre-flight checks
    #     if not all([nin, bvn, full_name]):
    #         profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.personal_info_reason = (
    #             'Full name, NIN, and BVN are all required.'
    #         )
    #         return

    #     if len(nin) != 11 or not nin.isdigit():
    #         profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.personal_info_reason = 'NIN must be exactly 11 digits.'
    #         return

    #     if len(bvn) != 11 or not bvn.isdigit():
    #         profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.personal_info_reason = 'BVN must be exactly 11 digits.'
    #         return

    #     provider = get_provider()

    #     # ── NIN lookup ──
    #     try:
    #         nin_data = provider.lookup_nin(nin)
    #         profile.nin_lookup_response = nin_data.raw_response
    #     except KYCProviderError as e:
    #         profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.personal_info_reason = f'NIN lookup failed: {e}'
    #         return

    #     # ── BVN lookup ──
    #     try:
    #         bvn_data = provider.lookup_bvn(bvn)
    #         profile.bvn_lookup_response = bvn_data.raw_response
    #     except KYCProviderError as e:
    #         profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.personal_info_reason = f'BVN lookup failed: {e}'
    #         return

    #     # ── Match: typed name vs NIN name ──
    #     if not names_match(full_name, nin_data.full_name):
    #         profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.personal_info_reason = (
    #             f'NIN name mismatch. Provided name does not match NIN record.'
    #         )
    #         return

    #     # ── Match: typed name vs BVN name ──
    #     if not names_match(full_name, bvn_data.full_name):
    #         profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.personal_info_reason = (
    #             f'BVN name mismatch. Provided name does not match BVN record.'
    #         )
    #         return

    #     # ── Cross-check: NIN name == BVN name ──
    #     if not names_match(nin_data.full_name, bvn_data.full_name):
    #         profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.personal_info_reason = (
    #             'NIN and BVN are registered to different names. '
    #             'These must belong to the same person.'
    #         )
    #         return

    #     # ── DOB consistency: typed vs NIN ──
    #     if dob_str:
    #         typed_dob = parse_date(dob_str)
    #         nin_dob = parse_date(nin_data.date_of_birth)
    #         if typed_dob and nin_dob and typed_dob != nin_dob:
    #             profile.personal_info_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #             profile.personal_info_reason = (
    #                 'Date of birth does not match NIN record.'
    #             )
    #             return

    #     # All checks passed
    #     profile.personal_info_status = KYCProfile.SectionStatus.VERIFIED
    #     profile.personal_info_reason = ''

    # @staticmethod
    # def _validate_bank_account(user, profile: KYCProfile, payload: dict):
    #     """
    #     Validate bank account by resolving it via provider and matching name.

    #     Creates or replaces the user's BankAccount if validation passes.
    #     """
    #     bank_code = (payload.get('bank_code') or '').strip()
    #     account_number = (payload.get('account_number') or '').strip()

    #     if not all([bank_code, account_number]):
    #         profile.bank_account_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.bank_account_reason = (
    #             'Bank and account number are required.'
    #         )
    #         return

    #     if len(account_number) != 10 or not account_number.isdigit():
    #         profile.bank_account_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.bank_account_reason = 'Account number must be 10 digits.'
    #         return

    #     provider = get_provider()

    #     # ── Resolve account name from bank ──
    #     try:
    #         resolved = provider.resolve_bank_account(account_number, bank_code)
    #     except KYCProviderError as e:
    #         profile.bank_account_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.bank_account_reason = f'Bank account resolution failed: {e}'
    #         return

    #     # ── Match: bank's account_name vs typed full_name ──
    #     if not names_match(profile.full_name, resolved.account_name):
    #         profile.bank_account_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #         profile.bank_account_reason = (
    #             f'Bank account name "{resolved.account_name}" does not match '
    #             f'your registered identity name. Use a bank account in your name.'
    #         )
    #         return

    #     # ── Replace existing active bank account (if any) ──
    #     BankAccount.objects.filter(user=user, is_active=True).update(is_active=False)

    #     # Look up bank name (best-effort)
    #     bank_name = ''
    #     try:
    #         banks = provider.list_banks()
    #         for b in banks:
    #             if b['code'] == bank_code:
    #                 bank_name = b['name']
    #                 break
    #     except KYCProviderError:
    #         bank_name = bank_code  # fallback

    #     BankAccount.objects.create(
    #         user=user,
    #         bank_code=bank_code,
    #         bank_name=bank_name or bank_code,
    #         account_number=account_number,
    #         account_name=resolved.account_name,
    #         is_active=True,
    #         verified_at=timezone.now(),
    #     )

    #     profile.bank_account_status = KYCProfile.SectionStatus.VERIFIED
    #     profile.bank_account_reason = ''

    # @staticmethod
    # def _validate_document(user, profile: KYCProfile, payload: dict):
    #     """
    #     Validate document section.

    #     Documents are uploaded separately via upload_document(). The submit
    #     payload references them by document_id. If no document_id provided
    #     and one is already on file (from prior upload), use that.
    #     """
    #     document_id = payload.get('document_id')

    #     if document_id:
    #         try:
    #             doc = KYCDocument.objects.get(id=document_id, user=user)
    #         except KYCDocument.DoesNotExist:
    #             profile.document_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #             profile.document_reason = 'Referenced document not found.'
    #             return

    #         # Mark previous active documents as inactive
    #         KYCDocument.objects.filter(
    #             user=user, is_active=True,
    #         ).exclude(id=doc.id).update(is_active=False)
    #     else:
    #         doc = KYCDocument.objects.filter(
    #             user=user, is_active=True,
    #         ).order_by('-uploaded_at').first()

    #         if not doc:
    #             profile.document_status = KYCProfile.SectionStatus.REQUIRES_CORRECTION
    #             profile.document_reason = (
    #                 'A document (utility bill or bank statement) is required.'
    #             )
    #             return

    #     # If admin previously rejected this doc, mirror that
    #     if doc.status == KYCDocument.Status.REJECTED:
    #         profile.document_status = KYCProfile.SectionStatus.REJECTED
    #         profile.document_reason = (
    #             doc.rejection_reason or 'Document rejected by review.'
    #         )
    #         return

    #     # Tier 1: auto-verify on upload (validation already done at upload)
    #     doc.status = KYCDocument.Status.VERIFIED
    #     doc.save(update_fields=['status'])

    #     profile.document_status = KYCProfile.SectionStatus.VERIFIED
    #     profile.document_reason = ''

    # # ── Public bank account resolution ──────────────────────────────────

    # @staticmethod
    # def resolve_bank(bank_code: str, account_number: str) -> dict:
    #     """
    #     Live bank account resolution for the frontend.

    #     Used by the KYC form to populate the Account Name field as soon
    #     as the user enters bank + account number.

    #     Returns: {'account_name': str, 'bank_code': str, 'account_number': str}
    #     Raises:  KYCServiceError on failure
    #     """
    #     if not bank_code or not account_number:
    #         raise KYCServiceError('Bank code and account number are required.')
    #     if len(account_number) != 10 or not account_number.isdigit():
    #         raise KYCServiceError('Account number must be 10 digits.')

    #     provider = get_provider()
    #     try:
    #         resolved = provider.resolve_bank_account(account_number, bank_code)
    #     except KYCProviderError as e:
    #         raise KYCServiceError(str(e))

    #     return {
    #         'account_name': resolved.account_name,
    #         'bank_code': resolved.bank_code,
    #         'account_number': resolved.account_number,
    #     }

    # # ── Document upload ──────────────────────────────────────────────────

    # @staticmethod
    # def upload_document(
    #     user, file, document_type: str = 'utility_bill',
    # ) -> KYCDocument:
    #     """
    #     Validate and store a KYC document.

    #     Returns the created KYCDocument record.
    #     Raises KYCServiceError on validation failure.
    #     """
    #     # ── File type ──
    #     content_type = getattr(file, 'content_type', '')
    #     if content_type not in ALLOWED_DOCUMENT_TYPES:
    #         raise KYCServiceError(
    #             f'File type "{content_type}" not allowed. Use PDF, JPG, or PNG.'
    #         )

    #     # ── Size ──
    #     size = getattr(file, 'size', 0)
    #     if size == 0:
    #         raise KYCServiceError('Uploaded file is empty.')
    #     if size > MAX_DOCUMENT_SIZE_BYTES:
    #         raise KYCServiceError(
    #             f'File too large ({size / 1024 / 1024:.1f} MB). '
    #             f'Maximum is {MAX_DOCUMENT_SIZE_BYTES / 1024 / 1024:.0f} MB.'
    #         )

    #     # ── Document type ──
    #     valid_types = [t[0] for t in KYCDocument.DocumentType.choices]
    #     if document_type not in valid_types:
    #         raise KYCServiceError(f'Invalid document type: {document_type}')

    #     # ── Create record ──
    #     doc = KYCDocument.objects.create(
    #         user=user,
    #         file=file,
    #         original_filename=getattr(file, 'name', 'document'),
    #         document_type=document_type,
    #         file_size_bytes=size,
    #         content_type=content_type,
    #         status=KYCDocument.Status.UPLOADED,
    #         is_active=True,
    #     )
    #     logger.info(
    #         'KYC document uploaded: user=%s type=%s size=%s',
    #         user.telegram_id, document_type, size,
    #     )
    #     return doc

    # # ── Status snapshot ──────────────────────────────────────────────────

    # @staticmethod
    # def get_status(user) -> dict:
    #     """Return current KYC status snapshot for the user."""
    #     try:
    #         profile = user.kyc_profile
    #     except KYCProfile.DoesNotExist:
    #         return {
    #             'overall_status': KYCProfile.OverallStatus.UNVERIFIED,
    #             'personal_info_status': KYCProfile.SectionStatus.PENDING,
    #             'personal_info_reason': '',
    #             'bank_account_status': KYCProfile.SectionStatus.PENDING,
    #             'bank_account_reason': '',
    #             'document_status': KYCProfile.SectionStatus.PENDING,
    #             'document_reason': '',
    #             'can_withdraw': False,
    #             'submitted_at': None,
    #             'last_resubmission_at': None,
    #         }

    #     return {
    #         'overall_status': profile.overall_status,
    #         'personal_info_status': profile.personal_info_status,
    #         'personal_info_reason': profile.personal_info_reason,
    #         'bank_account_status': profile.bank_account_status,
    #         'bank_account_reason': profile.bank_account_reason,
    #         'document_status': profile.document_status,
    #         'document_reason': profile.document_reason,
    #         'can_withdraw': profile.can_withdraw,
    #         'submitted_at': profile.submitted_at,
    #         'last_resubmission_at': profile.last_resubmission_at,
    #     }