"""
NOWPayments Mass Payouts provider — sends USDT to user crypto wallets.

Two modes (controlled by NOWPAYMENTS_PAYOUT_MODE env var):

  'mock' (default):
    - initiate_payout returns instant success with fake tx_hash
    - Useful for development before NowPayments KYB approval
    - Withdrawal marked completed immediately (no webhook needed)

  'live':
    - Real NowPayments API: POST /v1/payout
    - JWT auth + 2FA TOTP on every request
    - Returns 'processing' status; webhook fires when on-chain tx confirms
    - Requires pre-funded NowPayments USDT balance

Webhook signature: HMAC-SHA512 over sorted-keys JSON, header 'x-nowpayments-sig'
(same pattern as the deposit webhook).
"""
import hashlib
import hmac
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import requests
from django.conf import settings

from apps.withdrawals.models import Withdrawal
from apps.withdrawals.providers.base import PayoutProviderError



logger = logging.getLogger(__name__)


# Constants
NOWPAYMENTS_BASE = 'https://api.nowpayments.io/v1'
DEFAULT_TIMEOUT = 30  # payouts can be slow, larger than deposit timeout


@dataclass
class PayoutResult:
    """Result of initiating a crypto payout."""
    payout_id: str        # Provider's batch/payout ID
    tx_hash: str          # On-chain transaction hash (may be empty if processing)
    status: str           # 'completed' | 'processing' | 'failed'
    raw_response: dict


class NOWPaymentsPayoutProvider:
    """
    Mass Payouts provider — sends crypto from your NowPayments balance to user wallet.

    For now: USDT TRC-20 only (D8). Easy to extend later.
    """

    name = 'nowpayments_payout'

    def initiate_payout(self, withdrawal: Withdrawal) -> PayoutResult:
        """
        Send `withdrawal.amount` USDT to `withdrawal.wallet_address` on TRC-20.

        Returns PayoutResult. Raises PayoutProviderError on failure.
        """
        mode = getattr(settings, 'NOWPAYMENTS_PAYOUT_MODE', 'mock').lower()

        if mode == 'mock':
            return self._mock_payout(withdrawal)
        elif mode == 'live':
            return self._live_payout(withdrawal)
        else:
            raise PayoutProviderError(
                f'Unknown NOWPAYMENTS_PAYOUT_MODE: {mode!r}. Use "mock" or "live".'
            )

    # ─── Mock mode ───────────────────────────────────────────────────────

    def _mock_payout(self, withdrawal: Withdrawal) -> PayoutResult:
        """
        Simulate a successful payout. Useful for end-to-end testing without
        depending on NowPayments approval / pre-funded balance.

        Returns 'completed' immediately so the withdrawal lifecycle finishes.
        """
        # Tiny chance of mock failure for testing the failure path
        # (set MOCK_FAILURE_RATE=0.1 in env to simulate 10% failure)
        failure_rate = float(getattr(settings, 'NOWPAYMENTS_PAYOUT_MOCK_FAILURE_RATE', 0))
        if failure_rate > 0:
            # Deterministic: hash withdrawal ID for stable test behavior
            digest = hashlib.sha256(str(withdrawal.id).encode()).hexdigest()
            roll = int(digest[:8], 16) / (2 ** 32)
            if roll < failure_rate:
                logger.info(
                    'MOCK PAYOUT FAILURE (simulated): withdrawal=%s',
                    withdrawal.id,
                )
                raise PayoutProviderError(
                    'Mock payout failed (simulated). '
                    'Set NOWPAYMENTS_PAYOUT_MOCK_FAILURE_RATE=0 to disable.'
                )

        payout_id = f'MOCK_PAYOUT_{uuid.uuid4().hex[:16].upper()}'
        # Realistic-looking TRC-20 tx hash (64 hex chars)
        tx_hash = f'MOCK_TX_{secrets.token_hex(32)}'

        logger.info(
            'MOCK PAYOUT SUCCESS: withdrawal=%s amount=$%s address=%s...%s tx=%s',
            withdrawal.id, withdrawal.amount,
            withdrawal.wallet_address[:6], withdrawal.wallet_address[-6:],
            tx_hash[:20],
        )

        return PayoutResult(
            payout_id=payout_id,
            tx_hash=tx_hash,
            status='completed',
            raw_response={
                'mock': True,
                'payout_id': payout_id,
                'tx_hash': tx_hash,
                'note': 'Mock mode — no real funds moved.',
            },
        )

    # ─── Live mode ───────────────────────────────────────────────────────

    def _live_payout(self, withdrawal: Withdrawal) -> PayoutResult:
        """
        Real NowPayments Mass Payouts API call.

        Endpoint: POST /v1/payout
        Auth:
            - x-api-key:    NOWPAYMENTS_PAYOUT_API_KEY (separate from deposit key)
            - Authorization: Bearer <JWT>  (from NOWPAYMENTS_PAYOUT_JWT, refreshed periodically)
        Body includes 2FA TOTP code per request.
        """
        api_key = getattr(settings, 'NOWPAYMENTS_PAYOUT_API_KEY', '')
        jwt = getattr(settings, 'NOWPAYMENTS_PAYOUT_JWT', '')
        totp_code = getattr(settings, 'NOWPAYMENTS_PAYOUT_2FA_CODE', '')

        if not api_key or not jwt:
            raise PayoutProviderError(
                'NOWPayments payout not configured. Missing API key or JWT.'
            )

        if not totp_code:
            # In a real production setup, you'd use pyotp to generate this
            # from a stored TOTP secret. For now, expect it from env (rotated).
            raise PayoutProviderError(
                'NOWPayments 2FA code missing. Set NOWPAYMENTS_PAYOUT_2FA_CODE.'
            )

        ipn_url = self._compute_ipn_url()

        body = {
            'ipn_callback_url': ipn_url,
            'withdrawals': [{
                'address': withdrawal.wallet_address,
                'currency': 'usdttrc20',   # TRC-20 USDT
                'amount': float(withdrawal.amount),
                'unique_external_id': withdrawal.reference,  # for idempotency
            }],
        }

        headers = {
            'x-api-key': api_key,
            'Authorization': f'Bearer {jwt}',
            'Content-Type': 'application/json',
        }

        # 2FA code goes in body or header depending on NowPayments' current spec.
        # Their docs change — confirm at integration time.
        body['verification_code'] = totp_code

        try:
            resp = requests.post(
                f'{NOWPAYMENTS_BASE}/payout',
                json=body,
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            logger.error(
                'NOWPayments payout network error: withdrawal=%s err=%s',
                withdrawal.id, e,
            )
            raise PayoutProviderError(
                f'NOWPayments payout unreachable: {e}'
            )

        if resp.status_code >= 400:
            logger.error(
                'NOWPayments payout rejected: withdrawal=%s status=%s body=%s',
                withdrawal.id, resp.status_code, resp.text[:1000],
            )
            try:
                error_data = resp.json()
                error_msg = error_data.get('message') or error_data.get('error') or resp.text[:200]
            except (ValueError, AttributeError):
                error_msg = resp.text[:200]
            raise PayoutProviderError(f'NOWPayments rejected payout: {error_msg}')

        try:
            data = resp.json()
        except ValueError:
            raise PayoutProviderError(
                f'NOWPayments returned non-JSON: {resp.text[:500]}'
            )

        # NowPayments returns an array of payout items
        # We only sent one, so we read index 0
        items = data.get('withdrawals', [])
        if not items:
            raise PayoutProviderError(
                f'NOWPayments response missing withdrawals: {data}'
            )

        item = items[0]
        provider_status = item.get('status', '')
        payout_id = str(item.get('id', '') or item.get('batch_withdrawal_id', ''))
        tx_hash = item.get('hash', '') or ''

        logger.info(
            'NOWPayments payout initiated: withdrawal=%s payout_id=%s status=%s',
            withdrawal.id, payout_id, provider_status,
        )

        # NowPayments statuses map to ours
        if provider_status in ('finished', 'completed'):
            mapped = 'completed'
        elif provider_status in ('failed', 'rejected'):
            mapped = 'failed'
        else:
            # 'creating', 'sending', 'waiting' all map to 'processing'
            mapped = 'processing'

        return PayoutResult(
            payout_id=payout_id,
            tx_hash=tx_hash,
            status=mapped,
            raw_response=data,
        )

    # ─── Webhook signature ───────────────────────────────────────────────

    def verify_webhook_signature(
        self, payload_bytes: bytes, headers: dict,
    ) -> bool:
        """
        Verify webhook came from NowPayments. Same HMAC-SHA512 pattern as
        the deposit webhook.
        """
        signature = (
            headers.get('x-nowpayments-sig')
            or headers.get('HTTP_X_NOWPAYMENTS_SIG')
            or ''
        )
        if not signature:
            logger.warning('Payout webhook missing signature header')
            return False

        secret = getattr(settings, 'NOWPAYMENTS_PAYOUT_WEBHOOK_SECRET', '')
        if not secret:
            # Fall back to deposit IPN secret if payout secret not configured
            secret = getattr(settings, 'NOWPAYMENTS_IPN_SECRET', '')

        if not secret:
            logger.error('No NowPayments webhook secret configured — cannot verify')
            return False

        try:
            payload = json.loads(payload_bytes)
        except json.JSONDecodeError:
            return False

        sorted_payload = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        expected = hmac.new(
            secret.encode(),
            sorted_payload.encode(),
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, payload: dict) -> Optional[dict]:
        """
        Parse a payout-side webhook from NowPayments.

        Returns a dict that the webhook view can apply:
            {
                'event_type': 'completed' | 'failed' | 'processing',
                'reference': 'wd_xxx...',   # our withdrawal.reference (echo of unique_external_id)
                'tx_hash': '0xabc...',
                'raw': payload,
            }
        Or None if the event isn't actionable yet (still pending).
        """
        # NowPayments payout webhook structure (best-effort — confirm with their docs):
        # {
        #   "withdrawal_id": 123,
        #   "unique_external_id": "wd_xyz",   ← our reference
        #   "status": "finished" | "failed" | "sending" | ...
        #   "hash": "0x...",
        #   "amount": "20.0",
        #   ...
        # }

        external_id = payload.get('unique_external_id', '')
        if not external_id:
            logger.warning('Payout webhook missing unique_external_id: %s', payload)
            return None

        status = payload.get('status', '')

        if status in ('finished', 'completed'):
            event_type = 'completed'
        elif status in ('failed', 'rejected'):
            event_type = 'failed'
        elif status in ('creating', 'sending', 'waiting'):
            # Intermediate — frontend can ignore, we just update no fields
            return None
        else:
            logger.warning('Unknown payout status from NowPayments: %s', status)
            return None

        return {
            'event_type': event_type,
            'reference': external_id,
            'tx_hash': payload.get('hash', '') or '',
            'raw': payload,
        }

    # ─── Helpers ─────────────────────────────────────────────────────────

    def _compute_ipn_url(self) -> str:
        """Build the public URL where NowPayments POSTs payout webhooks."""
        mini_app_url = getattr(settings, 'MINI_APP_URL', '')
        if not mini_app_url:
            return 'https://api.spinrewardsgame.com/webhooks/nowpayments-payout/'
        # Same pattern as deposit IPN url
        api_url = mini_app_url.replace('https://app.', 'https://api.')
        return f'{api_url.rstrip("/")}/webhooks/nowpayments-payout/'