"""Withdrawal provider factory."""
from django.conf import settings


def get_provider():
    name = getattr(settings, 'WITHDRAWAL_PROVIDER', 'stub')
    if name == 'paystack':
        from .paystack import PaystackPayoutProvider
        return PaystackPayoutProvider()
    elif name == 'stub':
        from .stub import StubPayoutProvider
        return StubPayoutProvider()
    else:
        raise ValueError(f'Unknown withdrawal provider: {name}')