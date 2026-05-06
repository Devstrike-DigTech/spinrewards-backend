"""
Stub payout provider.

Test patterns:
  account_number ending '9999' → recipient creation fails
  reference containing '9999'  → transfer initiation fails
  reference containing '0000'  → transfer stays pending
  all others                   → success
"""
import logging
import secrets
from decimal import Decimal

from .base import (
    PayoutProvider,
    PayoutProviderError,
    RecipientResult,
    TransferResult,
)

logger = logging.getLogger(__name__)


class StubPayoutProvider(PayoutProvider):

    def create_recipient(self, account_number, bank_code, account_name):
        logger.info('STUB: create_recipient ***%s @ %s', account_number[-4:], bank_code)

        if account_number.endswith('9999'):
            raise PayoutProviderError('Stub: simulated recipient creation failure')

        return RecipientResult(
            recipient_id=f'STUB_REC_{secrets.token_hex(8)}',
            raw_response={'stub': True},
        )

    def initiate_transfer(self, recipient_id, amount, reference, reason=''):
        logger.info('STUB: initiate_transfer ₦%s ref=%s', amount, reference)

        if '9999' in reference:
            raise PayoutProviderError('Stub: simulated transfer initiation failure')

        if '0000' in reference:
            return TransferResult(
                transfer_id=f'STUB_TXR_{secrets.token_hex(8)}',
                status='pending',
                raw_response={'stub': True, 'note': 'simulated pending'},
            )

        return TransferResult(
            transfer_id=f'STUB_TXR_{secrets.token_hex(8)}',
            status='completed',
            raw_response={'stub': True, 'amount': str(amount)},
        )

    def verify_transfer(self, transfer_id):
        return TransferResult(
            transfer_id=transfer_id,
            status='completed',
            raw_response={'stub': True},
        )