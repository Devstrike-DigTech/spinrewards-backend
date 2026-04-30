"""
Monnify provider — bank transfer via dedicated virtual account.

Flow:
  1. User signs up → on first /deposits/info request (or eagerly), we call
     Monnify to reserve a virtual account number for them.
  2. User transfers any amount from any Nigerian bank to that account number.
  3. Monnify detects the inbound transfer → POSTs to our webhook.
  4. Backend verifies HMAC-SHA512, creates a Deposit row, credits wallet.

Note: unlike Paystack, the user does NOT initiate a deposit on Monnify —
they just transfer money. So `initiate_deposit` for Monnify just returns
the existing virtual account info wrapped in a synthetic Deposit row that's
purely informational (status stays PENDING; only the webhook actually
completes a real Deposit).

The Deposit row for a Monnify transfer is created when the webhook fires —
because we can't predict when or how much the user will transfer.
"""
import base64
import hashlib
import hmac
import logging
import uuid
from decimal import Decimal
from typing import Optional

import requests
from django.conf import settings

from apps.payments.models import Deposit, VirtualAccount
from common.exceptions import ProviderError
from .base import PaymentProvider

logger = logging.getLogger(__name__)

MONNIFY_BASE = 'https://sandbox.monnify.com'  # swap to https://api.monnify.com for prod


class MonnifyProvider(PaymentProvider):
    name = Deposit.Provider.MONNIFY

    # ─── Auth ─────────────────────────────────────────────────────────

    def _get_access_token(self) -> str:
        """
        Monnify uses Bearer auth with a token obtained from /auth/login.
        Token TTL is ~1 hour. For production, cache in Redis with TTL.
        For now, fetch fresh on each call.
        """
        api_key = settings.MONNIFY_API_KEY
        secret = settings.MONNIFY_SECRET_KEY
        if not api_key or not secret:
            raise ProviderError('Monnify is not configured.')

        creds = base64.b64encode(f'{api_key}:{secret}'.encode()).decode()
        try:
            resp = requests.post(
                f'{MONNIFY_BASE}/api/v1/auth/login',
                headers={'Authorization': f'Basic {creds}'},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error('Monnify auth HTTP error: %s', e)
            raise ProviderError('Monnify is currently unavailable.')

        token = data.get('responseBody', {}).get('accessToken')
        if not token:
            raise ProviderError('Monnify auth failed.')
        return token

    # ─── Virtual Account ──────────────────────────────────────────────

    def get_or_create_virtual_account(self, user) -> VirtualAccount:
        """
        Idempotently provision a Monnify reserved account for the user.
        Subsequent calls return the existing account.
        """
        existing = VirtualAccount.objects.filter(user=user).first()
        if existing:
            return existing

        token = self._get_access_token()
        contract_code = settings.MONNIFY_CONTRACT_CODE
        if not contract_code:
            raise ProviderError('MONNIFY_CONTRACT_CODE is not configured.')

        account_reference = f'VA-{user.id}'
        customer_email = f'tg-{user.telegram_id}@noreply.spinrewards.com'
        customer_name = (
            f'{user.first_name} {user.last_name}'.strip()
            or user.username
            or f'User {user.telegram_id}'
        )

        try:
            resp = requests.post(
                f'{MONNIFY_BASE}/api/v2/bank-transfer/reserved-accounts',
                json={
                    'accountReference': account_reference,
                    'accountName': f'SpinRewards/{customer_name[:30]}',
                    'currencyCode': 'NGN',
                    'contractCode': contract_code,
                    'customerEmail': customer_email,
                    'customerName': customer_name,
                    'getAllAvailableBanks': True,
                },
                headers={'Authorization': f'Bearer {token}'},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error('Monnify VA create HTTP error: %s', e)
            raise ProviderError('Failed to provision virtual account.')

        if not data.get('requestSuccessful'):
            logger.warning('Monnify VA rejected: %s', data)
            raise ProviderError(data.get('responseMessage', 'Monnify error'))

        body = data['responseBody']
        # Monnify returns multiple accounts (Wema, Sterling, etc.); we pick the first.
        accounts = body.get('accounts', [])
        if not accounts:
            raise ProviderError('Monnify returned no accounts.')
        primary = accounts[0]

        return VirtualAccount.objects.create(
            user=user,
            monnify_account_reference=account_reference,
            account_number=primary['accountNumber'],
            account_name=body.get('accountName', ''),
            bank_name=primary.get('bankName', ''),
            bank_code=primary.get('bankCode', ''),
            provider_data=body,
        )

    # ─── Provider interface ───────────────────────────────────────────

    def initiate_deposit(self, user, amount: Decimal) -> Deposit:
        """
        For Monnify, "initiating a deposit" means: ensure the user has a
        virtual account. We return a Deposit row that's purely informational
        — the real Deposit completes when the webhook fires with the actual
        transferred amount (which may differ from `amount`).

        We use a placeholder Deposit here so the frontend can show the user
        their VA details consistently with how it shows Paystack URLs.
        """
        va = self.get_or_create_virtual_account(user)
        internal_reference = f'MON-{uuid.uuid4().hex[:24].upper()}'

        return Deposit.objects.create(
            user=user,
            amount=amount,  # expected amount (informational)
            provider=Deposit.Provider.MONNIFY,
            internal_reference=internal_reference,
            payment_address=(
                f'Bank: {va.bank_name}\n'
                f'Account: {va.account_number}\n'
                f'Name: {va.account_name}'
            ),
            provider_data={'virtual_account_id': str(va.id)},
        )

    def verify_webhook_signature(
        self, payload_bytes: bytes, headers: dict
    ) -> bool:
        signature = headers.get('monnify-signature', '') or headers.get(
            'HTTP_MONNIFY_SIGNATURE', '')
        if not signature:
            return False
        secret = settings.MONNIFY_SECRET_KEY
        if not secret:
            logger.error('Monnify secret missing — cannot verify webhook')
            return False

        expected = hmac.new(
            secret.encode(), payload_bytes, hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, payload: dict) -> Optional[dict]:
        event_type = payload.get('eventType')
        if event_type != 'SUCCESSFUL_TRANSACTION':
            return None

        body = payload.get('eventData', {})
        # For inbound bank transfers, the "reference" we care about is the
        # virtual account that received the money — not a per-deposit ref.
        # We use Monnify's transactionReference as the deposit's identity.
        tx_ref = body.get('transactionReference', '')
        if not tx_ref:
            return None

        # The destination virtual account tells us WHICH user received.
        # Monnify returns this in `destinationAccountInformation`.
        dest = body.get('destinationAccountInformation', {})
        account_reference = dest.get('accountReference', '')

        amount = Decimal(str(body.get('amountPaid', '0')))

        return {
            'event_type': 'success',
            # Two-part: virtual account reference + Monnify's tx_ref
            # The webhook handler uses both: VA ref to find user, tx_ref for idempotency
            'reference': tx_ref,
            'virtual_account_reference': account_reference,
            'amount': amount,
            'raw': payload,
        }