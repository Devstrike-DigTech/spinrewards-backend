"""
NOWPayments provider — crypto deposits with USDT auto-conversion.

Flow:
  1. Frontend calls POST /api/v1/deposits/ with {amount: <NGN>, provider: 'nowpayments'}
  2. We compute USDT equivalent at the locked NGN rate (admin-configurable).
  3. We call NOWPayments /v1/payment to create a payment with auto-conversion to USDT.
  4. NOWPayments returns a deposit address (e.g. a TRC-20 address for USDT).
  5. Frontend shows QR code + address.
  6. User sends crypto.
  7. NOWPayments POSTs IPN webhook on confirmation.
  8. We verify signature (HMAC-SHA512 over sorted JSON), match by payment_id,
     credit wallet in NGN.

Pricing model:
  - User says "I want to deposit ₦5000".
  - We multiply ₦5000 / NGN_PER_USDT to get USDT amount required.
  - We tell NOWPayments "expect <usdt_amount> USDT".
  - On webhook, we credit ₦5000 to the wallet (the rate is locked at init).

If NGN/USDT rate is volatile, the user might over- or under-pay slightly.
NOWPayments will report the exact USDT received; we use that × rate for the
final NGN credit. This means: rate locked at INIT, but actual NGN credited
reflects actual USDT received.
"""
import hashlib
import hmac
import json
from django.core.cache import cache
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

NOWPAYMENTS_BASE = 'https://api.nowpayments.io/v1'

# TODO: pull from a Setting model or daily oracle update.
# For now, an admin-controlled constant. 1 USDT = ~1500 NGN as of writing.
NGN_PER_USDT_DEFAULT = Decimal('1500')


def get_ngn_per_usdt() -> Decimal:
    """
    Returns the locked NGN-per-USDT rate.

    For now: env var fallback to default. Eventually: read from Setting
    model populated by an admin or scheduled job.
    """
    rate_str = getattr(settings, 'NGN_PER_USDT_RATE', None)
    if rate_str:
        return Decimal(str(rate_str))
    return NGN_PER_USDT_DEFAULT


class NOWPaymentsProvider(PaymentProvider):
    name = Deposit.Provider.NOWPAYMENTS

    def initiate_deposit(self, user, amount: Decimal) -> Deposit:
        api_key = settings.NOWPAYMENTS_API_KEY
        if not api_key:
            raise ProviderError('NOWPayments is not configured.')

        ngn_per_usdt = get_ngn_per_usdt()
        usdt_amount = (amount / ngn_per_usdt).quantize(Decimal('0.00000001'))
        internal_reference = f'NOW-{uuid.uuid4().hex[:24].upper()}'

        try:
            resp = requests.post(
                f'{NOWPAYMENTS_BASE}/payment',
                json={
                    'price_amount': float(usdt_amount),
                    'price_currency': 'usdt',
                    'pay_currency': 'usdttrc20',  # TRC-20 USDT (low fees)
                    'order_id': internal_reference,
                    'order_description': f'SpinRewards deposit for user {user.id}',
                    'ipn_callback_url': f'{settings.MINI_APP_URL.replace("https://app.", "https://api.")}/api/v1/webhooks/nowpayments/',
                },
                headers={
                    'x-api-key': api_key,
                    'Content-Type': 'application/json',
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error('NOWPayments init HTTP error: %s', e)
            raise ProviderError('NOWPayments is currently unavailable.')

        if 'pay_address' not in data:
            logger.warning('NOWPayments rejected: %s', data)
            raise ProviderError(
                data.get('message', 'NOWPayments rejected the request.')
            )

        return Deposit.objects.create(
            user=user,
            amount=amount,
            provider=Deposit.Provider.NOWPAYMENTS,
            internal_reference=internal_reference,
            provider_reference=str(data.get('payment_id', '')),
            payment_address=data['pay_address'],
            original_amount=usdt_amount,
            original_currency='USDT',
            conversion_rate=ngn_per_usdt,
            provider_data={'init_response': data},
        )

    def verify_webhook_signature(
        self, payload_bytes: bytes, headers: dict
    ) -> bool:
        signature = headers.get('x-nowpayments-sig', '') or headers.get(
            'HTTP_X_NOWPAYMENTS_SIG', '')
        if not signature:
            return False
        ipn_secret = settings.NOWPAYMENTS_IPN_SECRET
        if not ipn_secret:
            logger.error('NOWPayments IPN secret missing — cannot verify webhook')
            return False

        # NOWPayments signs the JSON body re-serialized with sorted keys.
        try:
            payload = json.loads(payload_bytes)
        except json.JSONDecodeError:
            return False

        sorted_payload = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        expected = hmac.new(
            ipn_secret.encode(),
            sorted_payload.encode(),
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, payload: dict) -> Optional[dict]:
        # NOWPayments sends 'payment_status': 'finished' on success.
        # 'partially_paid', 'expired', 'failed' are other terminal states.
        status = payload.get('payment_status', '')
        order_id = payload.get('order_id', '')
        if not order_id:
            return None

        if status == 'finished':
            event_type = 'success'
        elif status in ('partially_paid', 'expired', 'failed'):
            event_type = 'failure'
        else:
            # 'waiting', 'confirming', 'sending' — intermediate states, ignore
            return None

        # 'price_amount' is what we asked for in USDT
        # 'actually_paid' is what the user actually sent (may differ slightly)
        actually_paid_usdt = Decimal(str(payload.get('actually_paid', '0')))

        return {
            'event_type': event_type,
            'reference': order_id,  # our internal_reference
            'amount': actually_paid_usdt,  # in USDT, NOT NGN
            'raw': payload,
        }

    # Cache the currencies list for 1 hour to avoid hammering NowPayments
    CURRENCIES_CACHE_KEY = 'nowpayments:currencies'
    CURRENCIES_CACHE_TTL = 60 * 60  # 1 hour
 
    @classmethod
    def list_currencies(cls) -> list:
        """
        Return a list of available crypto currencies for deposits.
 
        Each item:
            {
              'code': 'btc',
              'name': 'Bitcoin',
              'network': 'btc',
              'is_stable': False,
              'min_amount_usd': 1.0,
              'logo_url': 'https://...',
            }
 
        Cached for 1 hour.
        """
        cached = cache.get(cls.CURRENCIES_CACHE_KEY)
        if cached:
            return cached
 
        api_key = settings.NOWPAYMENTS_API_KEY
        if not api_key:
            raise ProviderError('NOWPayments is not configured.')
 
        try:
            resp = requests.get(
                f'{NOWPAYMENTS_BASE}/full-currencies',
                headers={'x-api-key': api_key},
                timeout=10,
            )
            resp.raise_for_status()
            body = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning('NowPayments currencies fetch failed: %s', e)
            raise ProviderError(f'Could not fetch currencies: {e}')
 
        # Parse and normalize
        currencies = []
        for c in body.get('currencies', []):
            if not c.get('enable', True):
                continue
            currencies.append({
                'code': c.get('code', '').lower(),
                'name': c.get('name', ''),
                'network': c.get('network', '').lower(),
                'is_stable': c.get('is_stable', False),
                'min_amount_usd': c.get('min_amount', 0),
                'logo_url': c.get('logo_url', ''),
            })
 
        cache.set(cls.CURRENCIES_CACHE_KEY, currencies, cls.CURRENCIES_CACHE_TTL)
        return currencies