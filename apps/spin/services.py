import secrets
import logging
from decimal import Decimal
from django.db import transaction as db_transaction

from common.exceptions import (
    InvalidStakeError,
    DuplicateRequestError,
    ConfigurationError,
)
from apps.wallet.services import WalletService
from apps.wallet.models import Transaction
from .models import RTPTier, RTPOutcome, SpinResult

logger = logging.getLogger(__name__)


def _select_outcome(outcomes: list[RTPOutcome]) -> tuple[RTPOutcome, Decimal]:
    """
    Select an outcome using cryptographically secure RNG.
    Maps random value (0–1) to outcome using cumulative probability ranges.
    Returns (selected_outcome, rng_value).
    """
    # Generate secure random integer in [0, 10^10) then normalise to [0, 1)
    rng_int = secrets.randbelow(10 ** 10)
    rng_value = Decimal(rng_int) / Decimal(10 ** 10)

    cumulative = Decimal('0')
    for outcome in outcomes:
        cumulative += outcome.probability / Decimal('100')
        if rng_value < cumulative:
            return outcome, rng_value

    # Floating-point edge case fallback
    return outcomes[-1], rng_value


class SpinService:

    @staticmethod
    @db_transaction.atomic
    def execute(user, stake: Decimal, idempotency_key: str) -> dict:
        """
        Execute a spin for the given user and stake amount.

        Rules:
        - Idempotent: same key returns the same result without re-processing.
        - Atomic: stake deduction and win credit in a single transaction.
        - Server-side only: outcome is generated here, never on client.
        """
        # 1. Idempotency check
        existing = SpinResult.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            logger.info('Duplicate spin request: key=%s', idempotency_key)
            return SpinService._format_result(existing, user)

        # 2. Find matching tier
        tier = RTPTier.objects.filter(
            stake_min__lte=stake,
            stake_max__gte=stake,
            is_active=True,
        ).first()

        if not tier:
            raise InvalidStakeError(
                f'No active tier for stake amount ₦{stake}.'
            )

        # 3. Load and validate active outcomes
        outcomes = list(tier.outcomes.filter(is_active=True).order_by('id'))
        if not outcomes:
            raise ConfigurationError('No active outcomes configured for this tier.')

        total_prob = sum(o.probability for o in outcomes)
        if abs(total_prob - Decimal('100')) > Decimal('0.01'):
            raise ConfigurationError(
                f'Tier "{tier.name}" probability sum is {total_prob}%, must be 100%.'
            )

        # 4. Deduct stake from coin balance
        stake_tx = WalletService.debit(
            user=user,
            amount=stake,
            balance_type='coin',
            tx_type=Transaction.Type.STAKE,
            reference_id=f'stake_{idempotency_key}',
            metadata={'idempotency_key': idempotency_key, 'tier_id': str(tier.id)},
        )

        # 5. Select outcome
        selected_outcome, rng_value = _select_outcome(outcomes)
        win_amount = (stake * selected_outcome.multiplier).quantize(Decimal('0.01'))

        # 6. Credit winnings (if any)
        win_tx = None
        if win_amount > 0:
            win_tx = WalletService.credit(
                user=user,
                amount=win_amount,
                balance_type='coin',
                tx_type=Transaction.Type.WIN,
                reference_id=f'win_{idempotency_key}',
                metadata={'idempotency_key': idempotency_key, 'tier_id': str(tier.id)},
            )

        # 7. Record spin result
        spin = SpinResult.objects.create(
            user=user,
            tier=tier,
            stake=stake,
            multiplier=selected_outcome.multiplier,
            win_amount=win_amount,
            outcome_label=selected_outcome.label,
            rng_value=rng_value,
            stake_transaction=stake_tx,
            win_transaction=win_tx,
            idempotency_key=idempotency_key,
        )

        logger.info(
            'SPIN user=%s stake=%s outcome=%s win=%s rng=%s',
            user.telegram_id, stake, selected_outcome.label, win_amount, rng_value,
        )

        return SpinService._format_result(spin, user)

    @staticmethod
    def _format_result(spin: SpinResult, user) -> dict:
        new_balance = WalletService.get_balance(user, 'coin')
        return {
            'spin_id': str(spin.id),
            'stake': str(spin.stake),
            'multiplier': str(spin.multiplier),
            'win_amount': str(spin.win_amount),
            'outcome_label': spin.outcome_label,
            'result': spin.result,
            'new_coin_balance': str(new_balance),
            'idempotency_key': spin.idempotency_key,
        }
