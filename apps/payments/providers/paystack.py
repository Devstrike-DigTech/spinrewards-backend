"""
Paystack provider — card / USSD / instant checkout flow.

Flow:
  1. Frontend calls POST /api/v1/deposits/  with {amount, provider: 'paystack'}
  2. Backend hits Paystack /transaction/initialize
  3. Backend stores the returned authorization_url + reference
  4. Frontend opens authorization_url in Telegram's in-app browser
  5. User pays
  6. Paystack POSTs to /api/v1/webhooks/paystack/
  7. Backend verifies HMAC-SHA512, finds Deposit by reference, credits wallet
"""
import hashlib
import hmac
import logging
import uuid
from decimal import Decimal
from typing import Optional

import requests
from django.conf import settings

from apps.payments.models import Deposit
from common.exceptions import ProviderError
from .base import PaymentProvider

logger = logging.getLogger(__name__)

PAYSTACK_BASE = 'https://api.paystack.co'


class PaystackProvider(PaymentProvider):
    name = Deposit.Provider.PAYSTACK

    def initiate_deposit(self, user, amount: Decimal) -> Deposit:
        if not settings.PAYSTACK_SECRET_KEY:
            raise ProviderError('Paystack is not configured.')

        internal_reference = f'PSK-{uuid.uuid4().hex[:24].upper()}'
        amount_kobo = int(amount * 100)  # Paystack uses kobo (1 NGN = 100 kobo)

        # Use a non-bouncing email even when user has none.
        email = f'tg-{user.telegram_id}@noreply.spinrewards.com'

        try:
            resp = requests.post(
                f'{PAYSTACK_BASE}/transaction/initialize',
                json={
                    'amount': amount_kobo,
                    'email': email,
                    'reference': internal_reference,
                    'currency': 'NGN',
                    'metadata': {
                        'user_id': str(user.id),
                        'telegram_id': user.telegram_id,
                    },
                    'callback_url': f'{settings.MINI_APP_URL}/deposit/callback',
                },
                headers={
                    'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
                    'Content-Type': 'application/json',
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error('Paystack initiation HTTP error: %s', e)
            raise ProviderError('Paystack is currently unavailable.')

        if not data.get('status'):
            logger.warning('Paystack init rejected: %s', data)
            raise ProviderError(data.get('message', 'Paystack rejected the request.'))

        # IMPORTANT: do NOT wrap in transaction.atomic — we just made a paid
        # external call and don't want it rolled back if the create fails.
        return Deposit.objects.create(
            user=user,
            amount=amount,
            provider=Deposit.Provider.PAYSTACK,
            internal_reference=internal_reference,
            payment_url=data['data']['authorization_url'],
            provider_data={'init_response': data['data']},
        )

    def verify_webhook_signature(
        self, payload_bytes: bytes, headers: dict
    ) -> bool:
        signature = headers.get('X-Paystack-Signature', '') or headers.get(
            'HTTP_X_PAYSTACK_SIGNATURE', '')
        if not signature:
            return False
        if not settings.PAYSTACK_SECRET_KEY:
            logger.error('Paystack secret key missing — cannot verify webhook')
            return False

        expected = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(),
            payload_bytes,
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, payload: dict) -> Optional[dict]:
        event = payload.get('event')
        data = payload.get('data', {})

        # We only act on charge events. Ignore all others (transfer events
        # are fired for outbound transfers, not inbound deposits).
        if event != 'charge.success':
            return None

        reference = data.get('reference', '')
        if not reference:
            return None

        amount_kobo = data.get('amount', 0)
        amount = Decimal(amount_kobo) / Decimal('100')

        return {
            'event_type': 'success' if data.get('status') == 'success' else 'failure',
            'reference': reference,
            'amount': amount,
            'raw': payload,
        }