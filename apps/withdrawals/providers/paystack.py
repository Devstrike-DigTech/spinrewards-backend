"""
Paystack Transfers provider.
Docs: https://paystack.com/docs/api/transfer/
"""
import logging
from decimal import Decimal

import requests
from django.conf import settings

from .base import (
    PayoutProvider,
    PayoutProviderError,
    RecipientResult,
    TransferResult,
)

logger = logging.getLogger(__name__)


def to_kobo(amount: Decimal) -> int:
    return int(amount * 100)


class PaystackPayoutProvider(PayoutProvider):

    @property
    def base_url(self) -> str:
        return 'https://api.paystack.co'

    @property
    def headers(self) -> dict:
        secret = getattr(settings, 'PAYSTACK_SECRET_KEY', '')
        if not secret:
            raise PayoutProviderError('PAYSTACK_SECRET_KEY is not configured.')
        return {
            'Authorization': f'Bearer {secret}',
            'Content-Type': 'application/json',
        }

    @property
    def timeout(self) -> int:
        return getattr(settings, 'PAYSTACK_TIMEOUT', 15)

    def _request(self, method, path, json_body=None):
        url = f'{self.base_url}{path}'
        try:
            response = requests.request(
                method, url, headers=self.headers,
                json=json_body or {}, timeout=self.timeout,
            )
        except requests.Timeout:
            raise PayoutProviderError(f'Paystack timeout: {path}')
        except requests.RequestException as e:
            raise PayoutProviderError(f'Paystack unreachable: {e}')

        try:
            body = response.json()
        except ValueError:
            raise PayoutProviderError(f'Paystack returned non-JSON ({response.status_code})')

        if response.status_code >= 500:
            raise PayoutProviderError(f'Paystack server error ({response.status_code})')

        if not body.get('status'):
            raise PayoutProviderError(f'Paystack: {body.get("message", "Unknown error")}')

        return body

    @staticmethod
    def _map_status(paystack_status: str) -> str:
        return {
            'success': 'completed',
            'pending': 'processing',
            'otp': 'processing',
            'failed': 'failed',
            'reversed': 'failed',
            'abandoned': 'failed',
        }.get(paystack_status.lower(), 'processing')

    def create_recipient(self, account_number, bank_code, account_name):
        body = self._request('POST', '/transferrecipient', {
            'type': 'nuban',
            'name': account_name,
            'account_number': account_number,
            'bank_code': bank_code,
            'currency': 'NGN',
        })

        data = body.get('data', {})
        recipient_code = data.get('recipient_code')
        if not recipient_code:
            raise PayoutProviderError('Paystack returned no recipient_code')

        return RecipientResult(recipient_id=recipient_code, raw_response=body)

    def initiate_transfer(self, recipient_id, amount, reference, reason=''):
        body = self._request('POST', '/transfer', {
            'source': 'balance',
            'amount': to_kobo(amount),
            'recipient': recipient_id,
            'reference': reference,
            'reason': reason or 'Spin Rewards Withdrawal',
        })

        data = body.get('data', {})
        transfer_code = data.get('transfer_code') or str(data.get('id', ''))

        return TransferResult(
            transfer_id=transfer_code,
            status=self._map_status(data.get('status', '')),
            raw_response=body,
        )

    def verify_transfer(self, transfer_id):
        body = self._request('GET', f'/transfer/verify/{transfer_id}')
        data = body.get('data', {})
        return TransferResult(
            transfer_id=transfer_id,
            status=self._map_status(data.get('status', '')),
            raw_response=body,
        )