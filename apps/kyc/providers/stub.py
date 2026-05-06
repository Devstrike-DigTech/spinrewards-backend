"""
Stub KYC provider for development and tests.

Returns plausible-looking data without calling any external API.

Test patterns the stub recognizes:
  - NIN/BVN ending in 9999 → simulates provider error (network/auth)
  - NIN/BVN ending in 0000 → returns 'MISMATCH NAME' (failure to match)
  - Account number ending in 9999 → bank not found
  - Account number ending in 0000 → returns 'WRONG PERSON'
  - All other inputs → returns 'JOHN DOE' / matches happy path

These patterns let smoke tests exercise every code path without network.
"""
import logging

from .base import (
    BankAccountResolveResult,
    IdentityLookupResult,
    KYCProvider,
    KYCProviderError,
)

logger = logging.getLogger(__name__)

DEFAULT_NAME = 'JOHN DOE'


class StubProvider(KYCProvider):
    """No-op provider for dev environments and smoke tests."""

    def lookup_nin(self, nin: str) -> IdentityLookupResult:
        logger.info('STUB: NIN lookup for %s***', nin[:4])

        if nin.endswith('9999'):
            raise KYCProviderError(f'Stub: simulated NIN lookup failure')

        name = 'MISMATCH NAME' if nin.endswith('0000') else DEFAULT_NAME
        first, _, last = name.partition(' ')

        return IdentityLookupResult(
            full_name=name,
            first_name=first,
            last_name=last or '',
            middle_name='',
            date_of_birth='1990-01-01',
            phone_number='08012345678',
            gender='M',
            raw_response={'stub': True, 'nin_prefix': nin[:4]},
        )

    def lookup_bvn(self, bvn: str) -> IdentityLookupResult:
        logger.info('STUB: BVN lookup for %s***', bvn[:4])

        if bvn.endswith('9999'):
            raise KYCProviderError(f'Stub: simulated BVN lookup failure')

        name = 'MISMATCH NAME' if bvn.endswith('0000') else DEFAULT_NAME
        first, _, last = name.partition(' ')

        return IdentityLookupResult(
            full_name=name,
            first_name=first,
            last_name=last or '',
            middle_name='',
            date_of_birth='1990-01-01',
            phone_number='08012345678',
            gender='M',
            raw_response={'stub': True, 'bvn_prefix': bvn[:4]},
        )

    def resolve_bank_account(
        self, account_number: str, bank_code: str,
    ) -> BankAccountResolveResult:
        logger.info(
            'STUB: bank account resolve for ***%s @ %s',
            account_number[-4:], bank_code,
        )

        if account_number.endswith('9999'):
            raise KYCProviderError(
                f'Stub: simulated bank account not found'
            )

        name = 'WRONG PERSON' if account_number.endswith('0000') else DEFAULT_NAME

        return BankAccountResolveResult(
            account_number=account_number,
            bank_code=bank_code,
            account_name=name,
            raw_response={'stub': True},
        )

    def list_banks(self) -> list[dict]:
        # Common Nigerian banks for dev/test
        return [
            {'code': '044', 'name': 'Access Bank'},
            {'code': '058', 'name': 'Guaranty Trust Bank'},
            {'code': '011', 'name': 'First Bank of Nigeria'},
            {'code': '057', 'name': 'Zenith Bank'},
            {'code': '033', 'name': 'United Bank for Africa'},
            {'code': '232', 'name': 'Sterling Bank'},
            {'code': '070', 'name': 'Fidelity Bank'},
            {'code': '076', 'name': 'Polaris Bank'},
            {'code': '035', 'name': 'Wema Bank'},
            {'code': '214', 'name': 'First City Monument Bank'},
            {'code': '215', 'name': 'Unity Bank'},
            {'code': '050', 'name': 'Ecobank Nigeria'},
            {'code': '221', 'name': 'Stanbic IBTC Bank'},
            {'code': '101', 'name': 'Providus Bank'},
            {'code': '50211', 'name': 'Kuda Bank'},
            {'code': '999992', 'name': 'OPay'},
            {'code': '50515', 'name': 'Moniepoint MFB'},
            {'code': '999991', 'name': 'PalmPay'},
        ]