# import secrets
# import logging
# from decimal import Decimal
# from django.db import transaction as db_transaction

# from common.exceptions import (
#     InvalidStakeError,
#     DuplicateRequestError,
#     ConfigurationError,
# )
# from apps.wallet.services import WalletService
# from apps.wallet.models import Transaction
# from .models import RTPTier, RTPOutcome, SpinResult

# logger = logging.getLogger(__name__)


# def _select_outcome(outcomes: list[RTPOutcome]) -> tuple[RTPOutcome, Decimal]:
#     """
#     Select an outcome using cryptographically secure RNG.
#     Maps random value (0–1) to outcome using cumulative probability ranges.
#     Returns (selected_outcome, rng_value).
#     """
#     # Generate secure random integer in [0, 10^10) then normalise to [0, 1)
#     rng_int = secrets.randbelow(10 ** 10)
#     rng_value = Decimal(rng_int) / Decimal(10 ** 10)

#     cumulative = Decimal('0')
#     for outcome in outcomes:
#         cumulative += outcome.probability / Decimal('100')
#         if rng_value < cumulative:
#             return outcome, rng_value

#     # Floating-point edge case fallback
#     return outcomes[-1], rng_value


# class SpinService:

#     @staticmethod
#     @db_transaction.atomic
#     def execute(user, stake: Decimal, idempotency_key: str) -> dict:
#         """
#         Execute a spin for the given user and stake amount.

#         Rules:
#         - Idempotent: same key returns the same result without re-processing.
#         - Atomic: stake deduction and win credit in a single transaction.
#         - Server-side only: outcome is generated here, never on client.
#         """
#         # 1. Idempotency check
#         existing = SpinResult.objects.filter(idempotency_key=idempotency_key).first()
#         if existing:
#             logger.info('Duplicate spin request: key=%s', idempotency_key)
#             return SpinService._format_result(existing, user)

#         # 2. Find matching tier
#         tier = RTPTier.objects.filter(
#             stake_min__lte=stake,
#             stake_max__gte=stake,
#             is_active=True,
#         ).first()

#         if not tier:
#             raise InvalidStakeError(
#                 f'No active tier for stake amount ₦{stake}.'
#             )

#         # 3. Load and validate active outcomes
#         outcomes = list(tier.outcomes.filter(is_active=True).order_by('id'))
#         if not outcomes:
#             raise ConfigurationError('No active outcomes configured for this tier.')

#         total_prob = sum(o.probability for o in outcomes)
#         if abs(total_prob - Decimal('100')) > Decimal('0.01'):
#             raise ConfigurationError(
#                 f'Tier "{tier.name}" probability sum is {total_prob}%, must be 100%.'
#             )

#         # 4. Deduct stake from coin balance
#         stake_tx = WalletService.debit(
#             user=user,
#             amount=stake,
#             balance_type='coin',
#             tx_type=Transaction.Type.STAKE,
#             reference_id=f'stake_{idempotency_key}',
#             metadata={'idempotency_key': idempotency_key, 'tier_id': str(tier.id)},
#         )

#         # 5. Select outcome
#         selected_outcome, rng_value = _select_outcome(outcomes)
#         win_amount = (stake * selected_outcome.multiplier).quantize(Decimal('0.01'))

#         # 6. Credit winnings (if any)
#         win_tx = None
#         if win_amount > 0:
#             win_tx = WalletService.credit(
#                 user=user,
#                 amount=win_amount,
#                 balance_type='coin',
#                 tx_type=Transaction.Type.WIN,
#                 reference_id=f'win_{idempotency_key}',
#                 metadata={'idempotency_key': idempotency_key, 'tier_id': str(tier.id)},
#             )

#         # 7. Record spin result
#         spin = SpinResult.objects.create(
#             user=user,
#             tier=tier,
#             stake=stake,
#             multiplier=selected_outcome.multiplier,
#             win_amount=win_amount,
#             outcome_label=selected_outcome.label,
#             rng_value=rng_value,
#             stake_transaction=stake_tx,
#             win_transaction=win_tx,
#             idempotency_key=idempotency_key,
#         )

#         logger.info(
#             'SPIN user=%s stake=%s outcome=%s win=%s rng=%s',
#             user.telegram_id, stake, selected_outcome.label, win_amount, rng_value,
#         )

#         return SpinService._format_result(spin, user)

#     @staticmethod
#     def _format_result(spin: SpinResult, user) -> dict:
#         new_balance = WalletService.get_balance(user, 'coin')
#         return {
#             'spin_id': str(spin.id),
#             'stake': str(spin.stake),
#             'multiplier': str(spin.multiplier),
#             'win_amount': str(spin.win_amount),
#             'outcome_label': spin.outcome_label,
#             'result': spin.result,
#             'new_coin_balance': str(new_balance),
#             'idempotency_key': spin.idempotency_key,
#         }

"""
Spin engine service.

Single public entry point: SpinEngine.execute(user, wheel_id, stake_amount).

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

Everything is in @transaction.atomic. If anything fails, the whole spin is
rolled back — no partial states.
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

    The integer weight approach has zero floating-point drift: probabilities
    are exact ratios, no rounding errors, no edge cases.
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

    # Unreachable — total_weight is sum of weights, and rng_int < total_weight.
    # But return the last segment defensively to make type-checkers happy.
    return segments[-1], rng_int, total_weight


# ─── Provably fair stubs ───────────────────────────────────────────────────

def _generate_seeds(user_id: str, nonce: int) -> tuple[str, str, int]:
    """
    Generate server seed + hash + use the existing nonce.

    For v1 we don't expose this to the user yet, but we store the values
    so a verification endpoint can be added later without re-architecting.
    """
    server_seed = secrets.token_hex(32)  # 64 hex chars, 256 bits of entropy
    server_seed_hash = hashlib.sha256(server_seed.encode()).hexdigest()
    return server_seed, server_seed_hash, nonce


# ─── Public service ────────────────────────────────────────────────────────

class SpinEngine:

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

        For Welcome wheel, stake_amount is ignored (free spin).
        For other wheels, stake_amount must be within the wheel's range.

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
            # Welcome spin is free — override any stake the client sent.
            stake_amount = Decimal('0')
        else:
            # Validate stake is within wheel's range.
            if stake_amount is None:
                raise InvalidStakeError('stake_amount is required.')
            if stake_amount < wheel.min_stake or stake_amount > wheel.max_stake:
                raise InvalidStakeError(
                    f'Stake ₦{stake_amount} is outside wheel range '
                    f'(₦{wheel.min_stake} – ₦{wheel.max_stake}).'
                )

        # ── 3. Load segments ──────────────────────────────────────────
        segments = list(
            wheel.segments.filter(is_active=True).order_by('position')
        )
        if not segments:
            raise ConfigurationError(
                f'Wheel "{wheel.name}" has no active segments.'
            )

        # ── 4. Generate spin reference (server-side, never client-supplied) ──
        spin_reference = f'spin_{secrets.token_urlsafe(24)}'

        # ── 5. Provably fair seeds (stub — not yet exposed to user) ───
        nonce = Spin.objects.filter(user=user).count()  # simple incrementing counter
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
        # payout_amount is the total amount the user "gets back" — for win,
        # this is stake + winnings. For push, it's the stake. For loss, 0.
        multiplier = segment.multiplier

        if is_welcome:
            # Welcome wheel: stake is 0, so multiplier × stake would always be 0.
            # Treat the segment's multiplier as a FLAT NGN PAYOUT in this case.
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
        """
        Apply the spin outcome to the wallet.

        Returns (outcome_string, resolution_transaction_or_None).

        Cases:
          - Welcome spin win    → credit cash directly (no lock to release)
          - multiplier == 0     → loss: forfeit lock
          - multiplier == 1     → push: release with 0 winnings
          - 0 < multiplier < 1  → partial: release with negative winnings (forfeit + partial credit)
          - multiplier > 1      → win: release with (multiplier - 1) × stake winnings
        """
        # ── Welcome spin: no lock to release. Just credit cash on win. ──
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

        # ── Loss: stake forfeited entirely ──
        if multiplier == 0:
            forfeit_tx = WalletService.forfeit(
                user=user,
                lock_reference_id=spin_reference,
                resolve_reference_id=f'{spin_reference}:resolve',
                metadata={'wheel': wheel.wheel_type, 'multiplier': '0'},
            )
            return Spin.Outcome.LOSS, forfeit_tx

        # ── Push: stake returned, no winnings ──
        if multiplier == Decimal('1'):
            release_tx = WalletService.release(
                user=user,
                lock_reference_id=spin_reference,
                winnings=Decimal('0'),
                resolve_reference_id=f'{spin_reference}:resolve',
                metadata={'wheel': wheel.wheel_type, 'multiplier': '1'},
            )
            return Spin.Outcome.PUSH, release_tx

        # ── Partial loss: 0 < multiplier < 1, e.g. 0.5x means half-back ──
        if multiplier < Decimal('1'):
            # Forfeit the entire stake first, then credit the partial payout.
            # This keeps the wallet's lock/release semantics clean: forfeit
            # closes the lock, then we issue a separate cash credit.
            WalletService.forfeit(
                user=user,
                lock_reference_id=spin_reference,
                resolve_reference_id=f'{spin_reference}:partial_forfeit',
                metadata={'wheel': wheel.wheel_type, 'multiplier': str(multiplier)},
            )
            # Credit the partial payout to cash.
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

        # ── Win: multiplier > 1 ──
        # User gets back stake + (multiplier - 1) × stake.
        # release() expects "winnings" = the amount ON TOP of the stake.
        winnings = (payout_amount - stake_amount).quantize(Decimal('0.01'))
        release_tx = WalletService.release(
            user=user,
            lock_reference_id=spin_reference,
            winnings=winnings,
            resolve_reference_id=f'{spin_reference}:resolve',
            metadata={'wheel': wheel.wheel_type, 'multiplier': str(multiplier)},
        )
        return Spin.Outcome.WIN, release_tx
