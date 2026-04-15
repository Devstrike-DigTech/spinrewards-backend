import logging
from datetime import date
from django.utils import timezone

from common.utils import encrypt
from common.exceptions import BankVerificationError
from apps.payments.services import PaymentService
from .models import KYC

logger = logging.getLogger(__name__)


class KYCService:

    @staticmethod
    def submit(
        user,
        full_name: str,
        nin: str,
        dob: date,
        bank_account: str,
        bank_code: str,
    ) -> KYC:
        """
        Submit KYC. Verifies bank account, encrypts NIN, saves as pending.
        If KYC exists and is rejected, it can be resubmitted.
        """
        # Verify bank account
        account_name = PaymentService.resolve_bank_account(bank_account, bank_code)

        kyc, _ = KYC.objects.update_or_create(
            user=user,
            defaults={
                'full_name': full_name,
                'nin_encrypted': encrypt(nin),
                'dob': dob,
                'bank_account': bank_account,
                'bank_code': bank_code,
                'account_name': account_name,
                'status': KYC.Status.PENDING,
                'rejection_reason': '',
                'reviewed_at': None,
                'reviewed_by': None,
            },
        )

        logger.info('KYC submitted: user=%s', user.telegram_id)
        return kyc

    @staticmethod
    def approve(kyc: KYC, admin_user) -> KYC:
        kyc.status = KYC.Status.APPROVED
        kyc.reviewed_at = timezone.now()
        kyc.reviewed_by = admin_user
        kyc.save(update_fields=['status', 'reviewed_at', 'reviewed_by'])

        from apps.notifications.tasks import notify_kyc_approved
        notify_kyc_approved.delay(kyc.user.telegram_id)

        KYCService._log_audit(kyc, admin_user, 'kyc_approved')
        logger.info('KYC approved: user=%s by=%s', kyc.user.telegram_id, admin_user.telegram_id)
        return kyc

    @staticmethod
    def reject(kyc: KYC, admin_user, reason: str) -> KYC:
        kyc.status = KYC.Status.REJECTED
        kyc.rejection_reason = reason
        kyc.reviewed_at = timezone.now()
        kyc.reviewed_by = admin_user
        kyc.save(update_fields=['status', 'rejection_reason', 'reviewed_at', 'reviewed_by'])

        from apps.notifications.tasks import notify_kyc_rejected
        notify_kyc_rejected.delay(kyc.user.telegram_id, reason)

        KYCService._log_audit(kyc, admin_user, 'kyc_rejected')
        logger.info('KYC rejected: user=%s by=%s', kyc.user.telegram_id, admin_user.telegram_id)
        return kyc

    @staticmethod
    def _log_audit(kyc: KYC, admin_user, action: str):
        from apps.admin_panel.models import AdminAuditLog
        AdminAuditLog.objects.create(
            admin=admin_user,
            action=action,
            target_model='KYC',
            target_id=str(kyc.id),
            new_state={'status': kyc.status, 'user': kyc.user.telegram_id},
        )
