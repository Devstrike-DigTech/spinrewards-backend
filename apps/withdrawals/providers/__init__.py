# """Withdrawal provider factory."""
# from django.conf import settings


# def get_provider():
#     name = getattr(settings, 'WITHDRAWAL_PROVIDER', 'stub')
#     if name == 'paystack':
#         from .paystack import PaystackPayoutProvider
#         return PaystackPayoutProvider()
#     elif name == 'stub':
#         from .stub import StubPayoutProvider
#         return StubPayoutProvider()
#     else:
#         raise ValueError(f'Unknown withdrawal provider: {name}')
"""
Withdrawal provider factory.

Two rails, each with its own provider:
  bank  → Paystack or stub (configurable via WITHDRAWAL_PROVIDER env)
  crypto → NOWPayments Mass Payouts (configurable via NOWPAYMENTS_PAYOUT_MODE: mock|live)

Use get_provider(rail) to dispatch.
"""
from django.conf import settings

from apps.withdrawals.models import Withdrawal


def get_provider(rail: str = None):
    """
    Return the appropriate payout provider for the given rail.

    rail='bank'   → PaystackPayoutProvider OR StubPayoutProvider
    rail='crypto' → NOWPaymentsPayoutProvider (mock or live mode by env)
    rail=None     → backward-compat: returns bank provider
    """
    rail = rail or Withdrawal.Rail.BANK

    if rail == Withdrawal.Rail.BANK:
        name = getattr(settings, 'WITHDRAWAL_PROVIDER', 'stub')
        if name == 'paystack':
            from .paystack import PaystackPayoutProvider
            return PaystackPayoutProvider()
        elif name == 'stub':
            from .stub import StubPayoutProvider
            return StubPayoutProvider()
        else:
            raise ValueError(f'Unknown bank withdrawal provider: {name}')

    elif rail == Withdrawal.Rail.CRYPTO:
        from .nowpayments_payout import NOWPaymentsPayoutProvider
        return NOWPaymentsPayoutProvider()

    else:
        raise ValueError(f'Unknown rail: {rail!r}')


# ─── Backward-compatibility ────────────────────────────────────────────
# If anything in the codebase calls get_provider() without an argument,
# it gets the bank provider. Confirmed safe — Chunk 4's _create_withdrawal
# only calls get_provider() from the bank rail path.