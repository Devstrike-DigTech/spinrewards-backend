"""
Abstract base class for payment providers.

Each provider (Paystack, Monnify, NOWPayments) implements this interface so
the upper layers (services, webhook views) treat them uniformly.
"""
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional

from apps.payments.models import Deposit


class PaymentProvider(ABC):
    """
    Contract every payment provider must satisfy.

    The provider knows how to:
      - Initiate a deposit (return URL or address)
      - Verify webhook signatures
      - Extract our reference from a webhook payload
      - Determine if the webhook indicates payment success
    """

    name: str = ''  # subclass sets to one of Deposit.Provider values

    @abstractmethod
    def initiate_deposit(self, user, amount: Decimal) -> Deposit:
        """
        Create a Deposit row, talk to the provider's API, populate the
        payment URL or address, and return the Deposit.

        Must be safe to call multiple times — but each call creates a NEW
        deposit row with a fresh internal_reference. (Idempotency for
        deposits is per-reference, not per-user.)
        """
        ...

    @abstractmethod
    def verify_webhook_signature(
        self, payload_bytes: bytes, headers: dict
    ) -> bool:
        """
        Verify the webhook signature. Returns True if valid, False otherwise.
        Must NOT raise — return False on any verification failure.
        """
        ...

    @abstractmethod
    def parse_webhook(self, payload: dict) -> Optional[dict]:
        """
        Extract from the webhook payload:
            {
                'event_type': 'success' | 'failure' | 'ignored',
                'reference': str,           # our internal_reference
                'amount': Decimal,           # amount paid
                'raw': dict,                 # full payload for storage
            }
        Return None if the webhook is not relevant (e.g. test ping, unrelated event).
        """
        ...