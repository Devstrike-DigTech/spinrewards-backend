"""
Spin engine service.

Public entry points:
    SpinEngine.execute(user, wheel_id, stake_amount)  — execute a spin
    SpinEngine.find_wheel_for_stake(stake_amount)     — locate the wheel
                                                        for a given stake

Wheel ranges follow inclusive-lower, exclusive-upper convention:
    [200, 500) — matches 200 to 499
    [500, 1000) — matches 500 to 999

What execute() does, in order:
    1. Validate inputs (wheel exists, active, stake within range).
    2. Validate welcome-spin uniqueness (DB constraint as backup).
    3. Generate cryptographic seeds (provably fair stub).
    4. Lock the stake from coin balance into staked balance (wallet.lock).
    5. Roll RNG → select segment based on integer weights.
    6. Resolve based on multiplier:
         multiplier == 0    → forfeit (loss)
         multiplier == 1    → release with 0 winnings (push)
         multiplier > 1     → release with (multiplier - 1) × stake winnings (win)
         0 < multiplier < 1 → forfeit + partial credit back (partial loss)
    7. Create Spin record linking everything.
"""
import hashlib
import logging
import secrets
from decimal import Decimal
from typing import Optional

from django.db import transaction as db_transaction

from apps.wallet.models import Transaction
from apps.wallet.services import WalletService
from common.exceptions import (
    ConfigurationError,
    InvalidStakeError,
    SpinRewardsException,
)

from .models import Spin, Wheel, WheelSegment

logger = logging.getLogger(__name__)


# ─── RNG helper ────────────────────────────────────────────────────────────

def _select_segment(segments: list[WheelSegment]) -> tuple[WheelSegment, int, int]:
    """
    Cryptographically secure segment selection using integer weights.
    Returns (selected_segment, rng_int, total_weight).
    """
    total_weight = sum(s.probability_weight for s in segments)
    if total_weight <= 0:
        raise ConfigurationError(
            'Wheel has no segments with positive probability weight.'
        )

    rng_int = secrets.randbelow(total_weight)

    cumulative = 0
    for segment in segments:
        cumulative += segment.probability_weight
        if rng_int < cumulative:
            return segment, rng_int, total_weight

    return segments[-1], rng_int, total_weight


# ─── Provably fair stubs ───────────────────────────────────────────────────

def _generate_seeds(user_id: str, nonce: int) -> tuple[str, str, int]:
    """Generate server seed + hash + use the existing nonce."""
    server_seed = secrets.token_hex(32)
    server_seed_hash = hashlib.sha256(server_seed.encode()).hexdigest()
    return server_seed, server_seed_hash, nonce


# ─── Public service ────────────────────────────────────────────────────────

class SpinEngine:

    @staticmethod
    def find_wheel_for_stake(stake_amount: Decimal) -> Optional[Wheel]:
        """
        Find the active non-welcome wheel that matches a stake amount.

        Uses inclusive-lower, exclusive-upper:
            min_stake <= stake_amount < max_stake

        Returns None if no wheel matches. Returns the unique matching
        wheel otherwise (overlap is prevented by Wheel.clean()).
        """
        return Wheel.objects.filter(
            is_active=True,
            is_welcome_only=False,
            min_stake__lte=stake_amount,
            max_stake__gt=stake_amount,
        ).first()

    @staticmethod
    @db_transaction.atomic
    def execute(
        user,
        wheel_id: str,
        stake_amount: Optional[Decimal] = None,
        client_seed: str = '',
    ) -> Spin:
        """
        Execute a spin atomically. Returns the created Spin row.

        Raises:
            InvalidStakeError — stake out of range, or wheel inactive
            ConfigurationError — wheel has no valid segments
            InsufficientFundsError — user can't afford the stake
            SpinRewardsException — welcome spin already used
        """
        # ── 1. Load and validate wheel ─────────────────────────────────
        try:
            wheel = Wheel.objects.select_for_update().get(
                id=wheel_id, is_active=True,
            )
        except Wheel.DoesNotExist:
            raise InvalidStakeError(f'Wheel not found or inactive: {wheel_id}')

        # ── 2. Welcome spin: enforce one-per-user (defense in depth) ──
        is_welcome = wheel.is_welcome_only
        if is_welcome:
            if Spin.objects.filter(user=user, is_welcome_spin=True).exists():
                raise SpinRewardsException(
                    'Welcome spin already used for this account.'
                )
            stake_amount = Decimal('0')
        else:
            # Validate stake matches wheel's range using inclusive-lower,
            # exclusive-upper semantics.
            if stake_amount is None:
                raise InvalidStakeError('stake_amount is required.')
            if not wheel.matches_stake(stake_amount):
                raise InvalidStakeError(
                    f'Stake ₦{stake_amount} is outside wheel range '
                    f'[₦{wheel.min_stake}, ₦{wheel.max_stake}).'
                )

        # ── 3. Load segments ──────────────────────────────────────────
        segments = list(
            wheel.segments.filter(is_active=True).order_by('position')
        )
        if not segments:
            raise ConfigurationError(
                f'Wheel "{wheel.name}" has no active segments.'
            )

        # ── 4. Generate spin reference ────────────────────────────────
        spin_reference = f'spin_{secrets.token_urlsafe(24)}'

        # ── 5. Provably fair seeds (stub) ─────────────────────────────
        nonce = Spin.objects.filter(user=user).count()
        server_seed, server_seed_hash, nonce = _generate_seeds(str(user.id), nonce)

        # ── 6. Lock the stake (skip for free Welcome spin) ────────────
        lock_tx = None
        if stake_amount > 0:
            lock_tx = WalletService.lock(
                user=user,
                amount=stake_amount,
                source_balance_type=wheel.currency_type,
                reference_id=spin_reference,
                metadata={
                    'wheel_id': str(wheel.id),
                    'wheel_type': wheel.wheel_type,
                },
            )

        # ── 7. Roll RNG → pick segment ────────────────────────────────
        segment, rng_int, total_weight = _select_segment(segments)
        rng_value = (
            Decimal(rng_int) / Decimal(total_weight)
            if total_weight > 0 else Decimal('0')
        ).quantize(Decimal('0.0000000001'))

        # ── 8. Resolve based on multiplier ────────────────────────────
        multiplier = segment.multiplier

        if is_welcome:
            # Welcome wheel: stake is 0, treat multiplier as flat NGN payout
            payout_amount = multiplier.quantize(Decimal('0.01'))
        else:
            payout_amount = (stake_amount * multiplier).quantize(Decimal('0.01'))

        outcome, resolution_tx = SpinEngine._resolve(
            user=user,
            wheel=wheel,
            stake_amount=stake_amount,
            multiplier=multiplier,
            payout_amount=payout_amount,
            spin_reference=spin_reference,
            is_welcome=is_welcome,
        )

        # ── 9. Create Spin record ─────────────────────────────────────
        spin = Spin.objects.create(
            user=user,
            wheel=wheel,
            stake_amount=stake_amount,
            segment_landed=segment,
            payout_amount=payout_amount,
            outcome=outcome,
            server_seed=server_seed,
            server_seed_hash=server_seed_hash,
            client_seed=client_seed,
            nonce=nonce,
            rng_value=rng_value,
            lock_transaction=lock_tx,
            resolution_transaction=resolution_tx,
            reference=spin_reference,
            is_welcome_spin=is_welcome,
        )

        logger.info(
            'SPIN user=%s wheel=%s stake=%s outcome=%s payout=%s',
            user.telegram_id, wheel.wheel_type, stake_amount, outcome,
            payout_amount,
        )
        return spin

    @staticmethod
    def _resolve(
        user, wheel, stake_amount, multiplier, payout_amount,
        spin_reference, is_welcome,
    ):
        """Apply the spin outcome to the wallet."""
        # Welcome spin: no lock to release. Just credit cash on win.
        if is_welcome:
            if payout_amount > 0:
                tx = WalletService.credit(
                    user=user,
                    amount=payout_amount,
                    balance_type='cash',
                    tx_type=Transaction.Type.WIN,
                    reference_id=f'{spin_reference}:welcome_payout',
                    metadata={'wheel': wheel.wheel_type, 'welcome': True},
                )
                return Spin.Outcome.WIN, tx
            else:
                return Spin.Outcome.LOSS, None

        # Loss: stake forfeited entirely
        if multiplier == 0:
            forfeit_tx = WalletService.forfeit(
                user=user,
                lock_reference_id=spin_reference,
                resolve_reference_id=f'{spin_reference}:resolve',
                metadata={'wheel': wheel.wheel_type, 'multiplier': '0'},
            )
            return Spin.Outcome.LOSS, forfeit_tx

        # Push: stake returned, no winnings
        if multiplier == Decimal('1'):
            release_tx = WalletService.release(
                user=user,
                lock_reference_id=spin_reference,
                winnings=Decimal('0'),
                resolve_reference_id=f'{spin_reference}:resolve',
                metadata={'wheel': wheel.wheel_type, 'multiplier': '1'},
            )
            return Spin.Outcome.PUSH, release_tx

        # Partial loss
        if multiplier < Decimal('1'):
            WalletService.forfeit(
                user=user,
                lock_reference_id=spin_reference,
                resolve_reference_id=f'{spin_reference}:partial_forfeit',
                metadata={'wheel': wheel.wheel_type, 'multiplier': str(multiplier)},
            )
            partial_tx = WalletService.credit(
                user=user,
                amount=payout_amount,
                balance_type='cash',
                tx_type=Transaction.Type.WIN,
                reference_id=f'{spin_reference}:partial_credit',
                metadata={
                    'wheel': wheel.wheel_type,
                    'multiplier': str(multiplier),
                    'note': 'partial_loss_payout',
                },
            )
            return Spin.Outcome.PARTIAL_LOSS, partial_tx

        # Win: multiplier > 1
        winnings = (payout_amount - stake_amount).quantize(Decimal('0.01'))
        release_tx = WalletService.release(
            user=user,
            lock_reference_id=spin_reference,
            winnings=winnings,
            resolve_reference_id=f'{spin_reference}:resolve',
            metadata={'wheel': wheel.wheel_type, 'multiplier': str(multiplier)},
        )
        return Spin.Outcome.WIN, release_tx