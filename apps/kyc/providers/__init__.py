"""
KYC provider implementations.

The base class defines the interface; stub.py is for development;
dojah.py is the production integration.

Configure which provider is active via settings.KYC_PROVIDER:
    'stub'  → development (auto-approves except for test patterns)
    'dojah' → production
"""
from django.conf import settings


def get_provider():
    """Return the configured KYC provider instance."""
    name = getattr(settings, 'KYC_PROVIDER', 'stub')
    if name == 'dojah':
        from .dojah import DojahProvider
        return DojahProvider()
    elif name == 'stub':
        from .stub import StubProvider
        return StubProvider()
    else:
        raise ValueError(f'Unknown KYC provider: {name}')