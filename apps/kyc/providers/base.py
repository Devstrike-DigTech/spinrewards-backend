"""
Base class for KYC providers.

Defines the interface that all provider implementations must satisfy.
Each method either returns the lookup data or raises a KYCProviderError.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class KYCProviderError(Exception):
    """Raised when a KYC provider call fails (network, auth, validation)."""
    pass


@dataclass
class IdentityLookupResult:
    """
    Standard shape for NIN/BVN lookup results.

    Providers may return partial data — all fields are best-effort.
    """
    full_name: str = ''
    first_name: str = ''
    last_name: str = ''
    middle_name: str = ''
    date_of_birth: str = ''         # ISO YYYY-MM-DD
    phone_number: str = ''
    gender: str = ''
    raw_response: dict = field(default_factory=dict)


@dataclass
class BankAccountResolveResult:
    """Standard shape for bank account name resolution."""
    account_number: str
    bank_code: str
    account_name: str
    raw_response: dict = field(default_factory=dict)


class KYCProvider(ABC):
    """Abstract interface for KYC providers."""

    @abstractmethod
    def lookup_nin(self, nin: str) -> IdentityLookupResult:
        """Look up a NIN against NIMC. Returns identity info."""
        ...

    @abstractmethod
    def lookup_bvn(self, bvn: str) -> IdentityLookupResult:
        """Look up a BVN against CBN. Returns identity info."""
        ...

    @abstractmethod
    def resolve_bank_account(
        self, account_number: str, bank_code: str,
    ) -> BankAccountResolveResult:
        """Resolve a Nigerian bank account to its registered name."""
        ...

    @abstractmethod
    def list_banks(self) -> list[dict]:
        """Return list of supported Nigerian banks. Each: {'code': X, 'name': Y}"""
        ...