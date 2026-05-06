"""Base class for withdrawal providers."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal


class PayoutProviderError(Exception):
    pass


@dataclass
class RecipientResult:
    recipient_id: str
    raw_response: dict = field(default_factory=dict)


@dataclass
class TransferResult:
    """status: 'pending' | 'processing' | 'completed' | 'failed'"""
    transfer_id: str
    status: str
    raw_response: dict = field(default_factory=dict)


class PayoutProvider(ABC):

    @abstractmethod
    def create_recipient(self, account_number: str, bank_code: str, account_name: str) -> RecipientResult:
        ...

    @abstractmethod
    def initiate_transfer(self, recipient_id: str, amount: Decimal, reference: str, reason: str = '') -> TransferResult:
        ...

    @abstractmethod
    def verify_transfer(self, transfer_id: str) -> TransferResult:
        ...