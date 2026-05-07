"""
Dojah KYC provider.

Dojah API docs: https://docs.dojah.io/

Endpoints used:
  GET  /api/v1/kyc/nin                                  NIN lookup
  GET  /api/v1/kyc/bvn                                  BVN lookup
  GET  /api/v1/general/banks                            List Nigerian banks
  GET  /api/v1/general/resolveaccountnumber             Resolve account name

  NOTE: Bank account resolution can ALSO be done via Paystack as a fallback
  since Paystack's /bank/resolve is very reliable. Set
  BANK_RESOLVE_PROVIDER=paystack in settings to use Paystack instead of Dojah
  for this specific step. Default is Dojah.

Authentication:
  Authorization: <DOJAH_SECRET_KEY>
  AppId:         <DOJAH_APP_ID>

Configure via Django settings:
  DOJAH_BASE_URL         Sandbox: https://sandbox.dojah.io
                         Production: https://api.dojah.io
  DOJAH_APP_ID
  DOJAH_SECRET_KEY
  DOJAH_TIMEOUT          Default 10 (seconds)
  BANK_RESOLVE_PROVIDER  'dojah' (default) or 'paystack'
"""
import logging

import requests
from django.conf import settings

from .base import (
    BankAccountResolveResult,
    IdentityLookupResult,
    KYCProvider,
    KYCProviderError,
)

logger = logging.getLogger(__name__)


class DojahProvider(KYCProvider):
    """Production KYC provider using Dojah."""

    @property
    def base_url(self) -> str:
        url = getattr(settings, 'DOJAH_BASE_URL', '').rstrip('/')
        if not url:
            raise KYCProviderError('DOJAH_BASE_URL is not configured.')
        return url

    @property
    def headers(self) -> dict:
        app_id = getattr(settings, 'DOJAH_APP_ID', '')
        secret_key = getattr(settings, 'DOJAH_SECRET_KEY', '')
        if not app_id or not secret_key:
            raise KYCProviderError(
                'DOJAH_APP_ID and DOJAH_SECRET_KEY must be configured.'
            )
        return {
            'Authorization': secret_key,
            'AppId': app_id,
            'Content-Type': 'application/json',
        }

    @property
    def timeout(self) -> int:
        return getattr(settings, 'DOJAH_TIMEOUT', 10)

    def _get(self, path: str, params: dict = None) -> dict:
        """Authenticated GET request to Dojah."""
        url = f'{self.base_url}{path}'
        try:
            response = requests.get(
                url,
                headers=self.headers,
                params=params or {},
                timeout=self.timeout,
            )
        except requests.Timeout:
            raise KYCProviderError(f'Dojah timeout: {path}')
        except requests.RequestException as e:
            raise KYCProviderError(f'Dojah unreachable: {e}')

        if response.status_code >= 500:
            raise KYCProviderError(
                f'Dojah server error ({response.status_code}): '
                f'{response.text[:200]}'
            )
        if response.status_code == 401:
            raise KYCProviderError('Dojah credentials invalid.')
        if response.status_code >= 400:
            try:
                msg = response.json().get('error', response.text[:200])
            except ValueError:
                msg = response.text[:200]
            raise KYCProviderError(f'Dojah error ({response.status_code}): {msg}')

        try:
            return response.json()
        except ValueError:
            raise KYCProviderError('Dojah returned non-JSON response')

    def lookup_nin(self, nin: str) -> IdentityLookupResult:
        if not nin or len(nin) != 11:
            raise KYCProviderError(
                f'NIN must be 11 digits, got: {len(nin) if nin else 0}'
            )

        body = self._get('/api/v1/kyc/nin', params={'nin': nin})
        entity = body.get('entity', {})

        if not entity:
            raise KYCProviderError(
                f'NIN lookup returned no entity data for {nin[:4]}***'
            )

        first = (entity.get('first_name', '') or '').strip().upper()
        middle = (entity.get('middle_name', '') or '').strip().upper()
        last = (entity.get('last_name', '') or '').strip().upper()
        full_name = ' '.join(p for p in [first, middle, last] if p)

        return IdentityLookupResult(
            full_name=full_name,
            first_name=first,
            last_name=last,
            middle_name=middle,
            date_of_birth=entity.get('date_of_birth', ''),
            phone_number=entity.get('phone_number', ''),
            gender=(entity.get('gender', '') or '').upper(),
            raw_response=body,
        )

    def lookup_bvn(self, bvn: str) -> IdentityLookupResult:
        if not bvn or len(bvn) != 11:
            raise KYCProviderError(
                f'BVN must be 11 digits, got: {len(bvn) if bvn else 0}'
            )

        body = self._get('/api/v1/kyc/bvn', params={'bvn': bvn})
        entity = body.get('entity', {})

        if not entity:
            raise KYCProviderError(
                f'BVN lookup returned no entity data for {bvn[:4]}***'
            )

        first = (entity.get('first_name', '') or '').strip().upper()
        middle = (entity.get('middle_name', '') or '').strip().upper()
        last = (entity.get('last_name', '') or '').strip().upper()
        full_name = ' '.join(p for p in [first, middle, last] if p)

        # BVN may return phone_number1 or phone_number depending on tier
        phone = (
            entity.get('phone_number1', '') or
            entity.get('phone_number', '')
        )

        return IdentityLookupResult(
            full_name=full_name,
            first_name=first,
            last_name=last,
            middle_name=middle,
            date_of_birth=entity.get('date_of_birth', ''),
            phone_number=phone,
            gender=(entity.get('gender', '') or '').upper(),
            raw_response=body,
        )

    def resolve_bank_account(
        self, account_number: str, bank_code: str,
    ) -> BankAccountResolveResult:
        if not account_number or len(account_number) != 10:
            raise KYCProviderError(
                f'Account number must be 10 digits, got: '
                f'{len(account_number) if account_number else 0}'
            )

        # Allow switching to Paystack for bank resolution — more reliable
        # for some banks. Set BANK_RESOLVE_PROVIDER=paystack in settings.
        resolve_provider = getattr(settings, 'BANK_RESOLVE_PROVIDER', 'dojah')
        if resolve_provider == 'paystack':
            return self._resolve_via_paystack(account_number, bank_code)

        return self._resolve_via_dojah(account_number, bank_code)

    def _resolve_via_dojah(
        self, account_number: str, bank_code: str,
    ) -> BankAccountResolveResult:
        """
        Resolve bank account via Dojah.

        Correct endpoint: /api/v1/general/resolveaccountnumber
        Params: account_number, bank_code
        """
        body = self._get(
            '/api/v1/general/resolveaccountnumber',
            params={
                'account_number': account_number,
                'bank_code': bank_code,
            },
        )
        entity = body.get('entity', {})

        if not entity:
            raise KYCProviderError(
                f'Bank account resolution returned no data: ***{account_number[-4:]}'
            )

        # Dojah may return account_name directly or nested
        account_name = (
            entity.get('account_name', '') or
            entity.get('name', '')
        )
        account_name = (account_name or '').strip().upper()

        if not account_name:
            raise KYCProviderError(
                f'Bank account resolution returned empty name: ***{account_number[-4:]}'
            )

        return BankAccountResolveResult(
            account_number=account_number,
            bank_code=bank_code,
            account_name=account_name,
            raw_response=body,
        )

    def _resolve_via_paystack(
        self, account_number: str, bank_code: str,
    ) -> BankAccountResolveResult:
        """
        Fallback: resolve bank account via Paystack /bank/resolve.

        More reliable for some Nigerian banks. Requires PAYSTACK_SECRET_KEY.
        """
        import requests as req

        paystack_secret = getattr(settings, 'PAYSTACK_SECRET_KEY', '')
        if not paystack_secret:
            raise KYCProviderError(
                'PAYSTACK_SECRET_KEY required for Paystack bank resolution.'
            )

        try:
            response = req.get(
                'https://api.paystack.co/bank/resolve',
                headers={
                    'Authorization': f'Bearer {paystack_secret}',
                    'Content-Type': 'application/json',
                },
                params={
                    'account_number': account_number,
                    'bank_code': bank_code,
                },
                timeout=10,
            )
        except req.Timeout:
            raise KYCProviderError('Paystack bank resolve timeout')
        except req.RequestException as e:
            raise KYCProviderError(f'Paystack bank resolve unreachable: {e}')

        try:
            body = response.json()
        except ValueError:
            raise KYCProviderError('Paystack bank resolve returned non-JSON')

        if not body.get('status'):
            raise KYCProviderError(
                f'Paystack bank resolve failed: {body.get("message", "unknown")}'
            )

        data = body.get('data', {})
        account_name = (data.get('account_name', '') or '').strip().upper()

        if not account_name:
            raise KYCProviderError(
                f'Paystack bank resolve returned empty name: ***{account_number[-4:]}'
            )

        return BankAccountResolveResult(
            account_number=account_number,
            bank_code=bank_code,
            account_name=account_name,
            raw_response=body,
        )

    def list_banks(self) -> list[dict]:
        body = self._get('/api/v1/general/banks')
        entities = body.get('entity', [])

        return [
            {'code': bank.get('code', ''), 'name': bank.get('name', '')}
            for bank in entities
            if bank.get('code') and bank.get('name')
        ]