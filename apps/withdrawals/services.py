# import logging
# from decimal import Decimal
# from django.db import transaction as db_transaction
# from django.utils import timezone

# from common.exceptions import KYCRequiredError, InsufficientFundsError
# from common.utils import generate_reference
# from apps.wallet.services import WalletService
# from apps.wallet.models import Transaction
# from apps.kyc.models import KYC
# from .models import WithdrawalRequest

# logger = logging.getLogger(__name__)


# class WithdrawalService:

#     @staticmethod
#     @db_transaction.atomic
#     def request(
#         user,
#         amount: Decimal,
#         bank_account: str,
#         bank_code: str,
#         account_name: str = '',
#     ) -> WithdrawalRequest:
#         """
#         Create a withdrawal request.
#         Validates KYC and balance, debits wallet, creates pending withdrawal.
#         Actual payout is triggered async via Celery.
#         """
#         # 1. KYC gate
#         kyc = getattr(user, 'kyc', None)
#         if not kyc or kyc.status != KYC.Status.APPROVED:
#             raise KYCRequiredError()

#         # 2. Balance check
#         cash_balance = WalletService.get_balance(user, 'cash')
#         if cash_balance < amount:
#             raise InsufficientFundsError('Insufficient cash balance for withdrawal.')

#         # 3. Generate reference
#         reference = generate_reference('WD')

#         # 4. Debit cash balance
#         tx = WalletService.debit(
#             user=user,
#             amount=amount,
#             balance_type='cash',
#             tx_type=Transaction.Type.WITHDRAWAL,
#             reference_id=reference,
#             metadata={'bank_account': bank_account, 'bank_code': bank_code},
#         )

#         # 5. Create withdrawal record
#         withdrawal = WithdrawalRequest.objects.create(
#             user=user,
#             amount=amount,
#             bank_account=bank_account,
#             bank_code=bank_code,
#             account_name=account_name,
#             reference=reference,
#             status=WithdrawalRequest.Status.PENDING,
#             transaction=tx,
#         )

#         logger.info(
#             'WITHDRAWAL_REQUESTED user=%s amount=%s ref=%s',
#             user.telegram_id, amount, reference,
#         )

#         # 6. Trigger async payout
#         from apps.withdrawals.tasks import process_withdrawal_payout
#         process_withdrawal_payout.delay(str(withdrawal.id))

#         return withdrawal

#     @staticmethod
#     @db_transaction.atomic
#     def reverse(withdrawal: WithdrawalRequest, reason: str) -> None:
#         """Restore funds when a payout fails."""
#         WalletService.credit(
#             user=withdrawal.user,
#             amount=withdrawal.amount,
#             balance_type='cash',
#             tx_type=Transaction.Type.REVERSAL,
#             reference_id=f'reversal_{withdrawal.reference}',
#             metadata={'reason': reason, 'original_ref': withdrawal.reference},
#         )

#         withdrawal.status = WithdrawalRequest.Status.REVERSED
#         withdrawal.failure_reason = reason
#         withdrawal.processed_at = timezone.now()
#         withdrawal.save(update_fields=['status', 'failure_reason', 'processed_at', 'updated_at'])

#         from apps.notifications.tasks import notify_withdrawal_failed
#         notify_withdrawal_failed.delay(
#             withdrawal.user.telegram_id, str(withdrawal.amount)
#         )

#         logger.info(
#             'WITHDRAWAL_REVERSED ref=%s reason=%s', withdrawal.reference, reason
#         )
