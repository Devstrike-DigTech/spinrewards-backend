import logging
import re
from typing import Optional
 
from django.db import transaction as db_transaction
from django.utils import timezone
 
from apps.withdrawals.models import CryptoWallet, Withdrawal
 
logger = logging.getLogger(__name__)
 
 
class CryptoWalletError(Exception):
    """Crypto wallet domain error with an optional error code."""
    def __init__(self, message, code='CRYPTO_WALLET_ERROR'):
        super().__init__(message)
        self.code = code
 
 
class CryptoWalletService:
 
    # TRC-20 format: starts with T, base58 chars only, total 34 chars
    # Reference: TRON addresses are base58check encoded, prefix byte 0x41
    TRC20_PATTERN = re.compile(r'^T[1-9A-HJ-NP-Za-km-z]{33}$')
 
    @staticmethod
    def validate_address(address: str, network: str = 'TRC20'):
        """
        Raise CryptoWalletError if address is malformed.
 
        TRC-20 rules:
          - Exactly 34 characters
          - Starts with capital T
          - Base58 alphabet (no 0, O, I, l)
        """
        if not address:
            raise CryptoWalletError(
                'wallet_address is required.',
                code='INVALID_WALLET_ADDRESS',
            )
 
        if network == 'TRC20':
            if not CryptoWalletService.TRC20_PATTERN.match(address):
                raise CryptoWalletError(
                    'Invalid TRC-20 USDT address format. Must be 34 characters '
                    'starting with T.',
                    code='INVALID_WALLET_ADDRESS',
                )
        else:
            raise CryptoWalletError(
                f'Network {network!r} not supported yet.',
                code='UNSUPPORTED_NETWORK',
            )
 
    @staticmethod
    @db_transaction.atomic
    def save(user, address: str, network: str = 'TRC20', label: str = '',
             set_default: bool = False) -> CryptoWallet:
        """
        Save (or re-activate) a crypto wallet for the user.
 
        If the address already exists for this user (active or inactive), return it.
        If inactive, re-activate it. Optionally make it default.
        """
        CryptoWalletService.validate_address(address, network)
 
        # Check for existing (including soft-deleted)
        existing = CryptoWallet.objects.filter(
            user=user, network=network, address=address,
        ).first()
 
        if existing:
            if not existing.is_active:
                existing.is_active = True
            if label:
                existing.label = label
            if set_default:
                CryptoWalletService._unset_other_defaults(user)
                existing.is_default = True
            existing.verified_at = existing.verified_at or timezone.now()
            existing.save()
            logger.info(
                'Crypto wallet re-activated: user=%s network=%s addr=%s...%s',
                user.telegram_id, network, address[:6], address[-6:],
            )
            return existing
 
        # New wallet
        if set_default:
            CryptoWalletService._unset_other_defaults(user)
 
        # If this is the user's FIRST active wallet, make it default automatically
        if not CryptoWallet.objects.filter(user=user, is_active=True).exists():
            set_default = True
 
        wallet = CryptoWallet.objects.create(
            user=user,
            network=network,
            address=address,
            label=label,
            is_default=set_default,
            is_active=True,
            verified_at=timezone.now(),
        )
        logger.info(
            'Crypto wallet saved: user=%s network=%s addr=%s...%s default=%s',
            user.telegram_id, network, address[:6], address[-6:], set_default,
        )
        return wallet
 
    @staticmethod
    def get_default(user, network: str = 'TRC20') -> Optional[CryptoWallet]:
        """Return the user's default active wallet for this network, or None."""
        return CryptoWallet.objects.filter(
            user=user, network=network, is_active=True, is_default=True,
        ).first()
 
    @staticmethod
    def list_for_user(user, network: str = ''):
        """Return all active wallets for the user, optionally filtered by network."""
        qs = CryptoWallet.objects.filter(user=user, is_active=True)
        if network:
            qs = qs.filter(network=network)
        return qs.order_by('-is_default', '-created_at')
 
    @staticmethod
    @db_transaction.atomic
    def deactivate(user, wallet_id):
        """Soft-delete a wallet (set is_active=False)."""
        wallet = CryptoWallet.objects.filter(
            user=user, id=wallet_id, is_active=True,
        ).first()
        if not wallet:
            raise CryptoWalletError(
                'Wallet not found.',
                code='WALLET_NOT_FOUND',
            )
        wallet.is_active = False
        wallet.is_default = False
        wallet.save()
        logger.info(
            'Crypto wallet deactivated: user=%s id=%s',
            user.telegram_id, wallet_id,
        )
        return wallet
 
    @staticmethod
    @db_transaction.atomic
    def set_default(user, wallet_id):
        """Make this wallet the user's default."""
        wallet = CryptoWallet.objects.filter(
            user=user, id=wallet_id, is_active=True,
        ).first()
        if not wallet:
            raise CryptoWalletError(
                'Wallet not found.',
                code='WALLET_NOT_FOUND',
            )
        CryptoWalletService._unset_other_defaults(user)
        wallet.is_default = True
        wallet.save()
        return wallet
 
    # ─── Helpers ─────────────────────────────────────────────────────
 
    @staticmethod
    def _unset_other_defaults(user):
        """Internal: clear is_default on all of this user's wallets."""
        CryptoWallet.objects.filter(user=user, is_default=True).update(
            is_default=False,
        )