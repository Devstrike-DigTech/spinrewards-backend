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
import hmac
import hashlib
import logging
import secrets
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.db import transaction as db_transaction

from apps.wallet.models import Transaction
from apps.wallet.services import WalletService
from common.exceptions import (
    ConfigurationError,
    InsufficientFundsError,
    InvalidStakeError,
    SpinRewardsException,
)

from .models import Spin, Wheel, WheelSegment
from apps.settings_app.models import SettingKey
from apps.settings_app.services import get_setting
from apps.notifications.services import NotificationService

logger = logging.getLogger(__name__)

# Map source_wallet (frontend value) → BalanceType (DB value)
SOURCE_WALLET_TO_BALANCE_TYPE = {
    'crypto_coins': Transaction.BalanceType.CRYPTO_COINS,
    'naira_coins': Transaction.BalanceType.NAIRA_COINS,
    'bonus_coins': Transaction.BalanceType.BONUS_COINS,
}
 
# Map source_wallet → which currency the source represents
SOURCE_WALLET_TO_CURRENCY = {
    'crypto_coins': Transaction.Currency.USDT,
    'naira_coins': Transaction.Currency.NGN,
    'bonus_coins': Transaction.Currency.NGN,   # bonus coins are platform-issued; default tag
}
 
# Map source_wallet → which withdraw balance wins land in (for NON-bonus sources)
SOURCE_WALLET_TO_WIN_DESTINATION = {
    'crypto_coins': Transaction.BalanceType.CRYPTO_WITHDRAW,
    'naira_coins': Transaction.BalanceType.NAIRA_WITHDRAW,
}
 
# Map bonus_destination (frontend value) → withdraw balance type
BONUS_DESTINATION_TO_BALANCE_TYPE = {
    'crypto': Transaction.BalanceType.CRYPTO_WITHDRAW,
    'naira': Transaction.BalanceType.NAIRA_WITHDRAW,
}
 
# Map bonus_destination → which BONUS_TO_*_RATE setting to use
BONUS_DESTINATION_TO_RATE_SETTING = {
    'crypto': SettingKey.BONUS_TO_USDT_RATE,
    'naira': SettingKey.BONUS_TO_NGN_RATE,
}
 
# Map bonus_destination → currency
BONUS_DESTINATION_TO_CURRENCY = {
    'crypto': Transaction.Currency.USDT,
    'naira': Transaction.Currency.NGN,
}

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


class SpinEngine:
 
    VALID_SOURCE_WALLETS = ('naira_coins', 'bonus_coins')
 
    # Map source_wallet string → BalanceType enum value
    SOURCE_WALLET_TO_BALANCE_TYPE = {
        'naira_coins': Transaction.BalanceType.NAIRA_COINS,
        'bonus_coins':   Transaction.BalanceType.BONUS_COINS,
    }
 
    @staticmethod
    def find_wheel_for_stake(stake_amount: Decimal) -> Optional[Wheel]:
        """
        Find the active non-welcome wheel that matches a stake amount.
        Inclusive-lower, exclusive-upper: min_stake <= stake < max_stake.
        """
        return Wheel.objects.filter(
            is_active=True,
            is_welcome_only=False,
            min_stake__lte=stake_amount,
            max_stake__gt=stake_amount,
        ).first()
 
    # @staticmethod
    # @db_transaction.atomic
    # # def execute(
    #     user,
    #     wheel_id: str,
    #     stake_amount: Optional[Decimal] = None,
    #     client_seed: str = '',
    #     source_wallet: str = 'deposit_coins',
    # ) -> Spin:
    #     """
    #     Execute a spin atomically.
 
    #     Args:
    #         user: the User
    #         wheel_id: UUID of the wheel
    #         stake_amount: how many coins to stake (ignored for welcome wheel)
    #         client_seed: optional client-provided seed for provably fair
    #         source_wallet: 'deposit_coins' or 'bonus_coins' — which balance funds the spin.
    #                        Ignored for welcome spin (which is free).
 
    #     Raises:
    #         InvalidStakeError — bad stake, bad wheel, or bad source_wallet
    #         ConfigurationError — wheel has no segments
    #         InsufficientFundsError — not enough in chosen wallet
    #         SpinRewardsException — welcome already used
    #     """
    #     # ── 1. Validate source_wallet ──────────────────────────────────
    #     if source_wallet not in SpinEngine.VALID_SOURCE_WALLETS:
    #         raise InvalidStakeError(
    #             f'Invalid source_wallet: {source_wallet!r}. '
    #             f'Must be one of {SpinEngine.VALID_SOURCE_WALLETS}.'
    #         )
    #     source_balance_type = SpinEngine.SOURCE_WALLET_TO_BALANCE_TYPE[source_wallet]
 
    #     # ── 2. Load and validate wheel ─────────────────────────────────
    #     try:
    #         wheel = Wheel.objects.select_for_update().get(
    #             id=wheel_id, is_active=True,
    #         )
    #     except Wheel.DoesNotExist:
    #         raise InvalidStakeError(f'Wheel not found or inactive: {wheel_id}')
 
    #     # ── 3. Welcome spin: enforce one-per-user (defense in depth) ──
    #     is_welcome = wheel.is_welcome_only
    #     if is_welcome:
    #         if Spin.objects.filter(user=user, is_welcome_spin=True).exists():
    #             raise SpinRewardsException(
    #                 'Welcome spin already used for this account.'
    #             )
    #         stake_amount = Decimal('0')
    #     else:
    #         if stake_amount is None:
    #             raise InvalidStakeError('stake_amount is required.')
    #         if not wheel.matches_stake(stake_amount):
    #             raise InvalidStakeError(
    #                 f'Stake {stake_amount} is outside wheel range '
    #                 f'[{wheel.min_stake}, {wheel.max_stake}).'
    #             )
 
    #     # ── 4. Generate spin reference ────────────────────────────────
    #     spin_reference = f'spin_{secrets.token_urlsafe(24)}'
 
    #     # ── 5. Provably fair seeds ────────────────────────────────────
    #     nonce = Spin.objects.filter(user=user).count()
    #     server_seed, server_seed_hash, nonce = _generate_seeds(str(user.id), nonce)
 
    #     # ── 6. Lock the stake from the CHOSEN wallet ──────────────────
    #     lock_tx = None
    #     if stake_amount > 0:
    #         lock_tx = WalletService.lock(
    #             user=user,
    #             amount=stake_amount,
    #             source_balance_type=source_balance_type,
    #             reference_id=spin_reference,
    #             metadata={
    #                 'wheel_id': str(wheel.id),
    #                 'wheel_type': wheel.wheel_type,
    #                 'source_wallet': source_wallet,
    #             },
    #         )
 
    #     # ── 7. Roll RNG → pick segment ────────────────────────────────
    #     segments = list(
    #         wheel.segments.filter(is_active=True).order_by('position')
    #     )
    #     if not segments:
    #         raise ConfigurationError(
    #             f'Wheel "{wheel.name}" has no active segments.'
    #         )
 
    #     segment, rng_int, total_weight = _select_segment(segments)
    #     rng_value = (
    #         Decimal(rng_int) / Decimal(total_weight)
    #         if total_weight > 0 else Decimal('0')
    #     ).quantize(Decimal('0.0000000001'))
 
    #     # ── 8. Resolve based on multiplier ────────────────────────────
    #     multiplier = segment.multiplier
 
    #     if is_welcome:
    #         payout_amount = multiplier.quantize(Decimal('0.01'))
    #     else:
    #         payout_amount = (stake_amount * multiplier).quantize(Decimal('0.01'))
 
    #     outcome, resolution_tx = SpinEngine._resolve(
    #         user=user,
    #         wheel=wheel,
    #         stake_amount=stake_amount,
    #         multiplier=multiplier,
    #         payout_amount=payout_amount,
    #         spin_reference=spin_reference,
    #         is_welcome=is_welcome,
    #         source_wallet=source_wallet,
    #         source_balance_type=source_balance_type,
    #     )
 
    #     # ── 9. Create Spin record ─────────────────────────────────────
    #     spin = Spin.objects.create(
    #         user=user,
    #         wheel=wheel,
    #         stake_amount=stake_amount,
    #         segment_landed=segment,
    #         payout_amount=payout_amount,
    #         outcome=outcome,
    #         server_seed=server_seed,
    #         server_seed_hash=server_seed_hash,
    #         client_seed=client_seed,
    #         nonce=nonce,
    #         rng_value=rng_value,
    #         lock_transaction=lock_tx,
    #         resolution_transaction=resolution_tx,
    #         reference=spin_reference,
    #         is_welcome_spin=is_welcome,
    #         source_wallet=source_wallet,
    #     )
 
    #     logger.info(
    #         'SPIN user=%s wheel=%s source=%s stake=%s outcome=%s payout=%s',
    #         user.telegram_id, wheel.wheel_type, source_wallet, stake_amount,
    #         outcome, payout_amount,
    #     )
    #     return spin
    # class SpinEngine:
    # """Owns the full lifecycle of a single spin: lock → spin → resolve."""
 
    @staticmethod
    @db_transaction.atomic
    def execute(
        user,
        wheel_id,
        stake_amount: Decimal,
        source_wallet: str = 'naira_coins',
        bonus_destination: str = '',
        client_seed: str = '',
    ):
        """
        Execute one full spin.
 
        Args:
            user: the user spinning
            wheel_id: which Wheel they're spinning
            stake_amount: how much they want to stake (in source_wallet units)
            source_wallet: 'crypto_coins' | 'naira_coins' | 'bonus_coins'
            bonus_destination: 'crypto' | 'naira' — REQUIRED if source_wallet=bonus_coins
            client_seed: optional client entropy
 
        Returns:
            (spin, resolution_tx) — the Spin record and its resolution Transaction
 
        Raises:
            InvalidStakeError    — bad source_wallet, missing bonus_destination,
                                   stake outside wheel range
            InsufficientFundsError — user doesn't have enough in source_wallet
            ValueError           — wheel inactive / not found
        """
        # ─── Validate source_wallet ──────────────────────────────────────
        if source_wallet not in SOURCE_WALLET_TO_BALANCE_TYPE:
            raise InvalidStakeError(
                f'Invalid source_wallet: {source_wallet!r}. '
                f'Must be one of: {list(SOURCE_WALLET_TO_BALANCE_TYPE.keys())}.'
            )
        source_balance_type = SOURCE_WALLET_TO_BALANCE_TYPE[source_wallet]
 
        # ─── Validate bonus_destination if bonus spin ────────────────────
        if source_wallet == 'bonus_coins':
            if bonus_destination not in BONUS_DESTINATION_TO_BALANCE_TYPE:
                raise InvalidStakeError(
                    f'bonus_destination required for bonus spins; '
                    f"got {bonus_destination!r}. Must be 'crypto' or 'naira'."
                )
 
        # ─── Load wheel ──────────────────────────────────────────────────
        try:
            wheel = Wheel.objects.select_for_update().get(id=wheel_id, is_active=True)
        except Wheel.DoesNotExist:
            raise ValueError(f'Wheel {wheel_id} not found or inactive.')
 
        # ─── Validate stake against wheel range ──────────────────────────
        if stake_amount < wheel.min_stake or stake_amount > wheel.max_stake:
            raise InvalidStakeError(
                f'Stake {stake_amount} outside wheel range '
                f'[{wheel.min_stake}, {wheel.max_stake}].'
            )
 
        # ─── Generate reference + RNG ────────────────────────────────────
        spin_reference = f'spin_{secrets.token_urlsafe(24)}'
 
        # Provably fair (server seed + client seed + nonce) — same as v2
        server_seed = secrets.token_hex(32)
        server_seed_hash = hashlib.sha256(server_seed.encode()).hexdigest()
 
        # Nonce = count of user's prior spins (deterministic per user)
        nonce = Spin.objects.filter(user=user).count()
 
        # Compute the RNG value: HMAC-SHA256(server_seed, f"{client_seed}:{nonce}")
        seed_input = f'{client_seed}:{nonce}'.encode()
        digest = hmac.new(server_seed.encode(), seed_input, hashlib.sha256).hexdigest()
        rng_int = int(digest[:16], 16)
        rng_value = Decimal(rng_int) / Decimal(2 ** 64)
 
        # ─── Lock the stake from source wallet ───────────────────────────
        lock_tx = WalletService.lock(
            user=user,
            amount=stake_amount,
            source_balance_type=source_balance_type,
            reference_id=spin_reference,
            metadata={
                'wheel_id': str(wheel.id),
                'wheel_type': wheel.wheel_type,
                'source_wallet': source_wallet,
                'bonus_destination': bonus_destination,
            },
        )
 
        # ─── Pick segment using RNG + segment weights ────────────────────
        segments = list(
            WheelSegment.objects.filter(wheel=wheel, is_active=True)
            .order_by('position')
        )
        if not segments:
            raise ValueError(f'Wheel {wheel.id} has no active segments.')
 
        total_weight = sum(s.probability_weight for s in segments)
        target = rng_value * Decimal(total_weight)
        cumulative = Decimal(0)
        segment_landed = segments[-1]  # fallback
        for s in segments:
            cumulative += Decimal(s.probability_weight)
            if target < cumulative:
                segment_landed = s
                break
 
        multiplier = segment_landed.multiplier
        payout_amount = (stake_amount * multiplier).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP,
        )
 
        # ─── Resolve (credit / forfeit / refund) ─────────────────────────
        outcome, resolution_tx, resolution_meta = SpinEngine._resolve(
            user=user,
            wheel=wheel,
            spin_reference=spin_reference,
            stake_amount=stake_amount,
            multiplier=multiplier,
            payout_amount=payout_amount,
            source_wallet=source_wallet,
            source_balance_type=source_balance_type,
            bonus_destination=bonus_destination,
        )
 
        # ─── Save the Spin record ────────────────────────────────────────
        spin = Spin.objects.create(
            user=user,
            wheel=wheel,
            stake_amount=stake_amount,
            segment_landed=segment_landed,
            payout_amount=payout_amount,
            outcome=outcome,
            source_wallet=source_wallet,
            bonus_destination=bonus_destination if source_wallet == 'bonus_coins' else '',
            payout_currency=resolution_meta.get('payout_currency', ''),
            credited_balance=resolution_meta.get('credited_balance', ''),
            net_credited=resolution_meta.get('net_credited', Decimal('0')),
            server_seed=server_seed,
            server_seed_hash=server_seed_hash,
            client_seed=client_seed,
            nonce=nonce,
            rng_value=rng_value,
            lock_transaction=lock_tx,
            resolution_transaction=resolution_tx,
            reference=spin_reference,
            is_welcome_spin=False,
        )
 
        logger.info(
            'SPIN user=%s wheel=%s source=%s stake=%s mult=%s outcome=%s payout=%s %s',
            user.telegram_id, wheel.wheel_type, source_wallet,
            stake_amount, multiplier, outcome, payout_amount,
            resolution_meta.get('payout_currency', ''),
        )
 
        return spin, resolution_tx
 
    # @staticmethod
    # def _resolve(
    #     user, wheel, stake_amount, multiplier, payout_amount,
    #     spin_reference, is_welcome, source_wallet, source_balance_type,
    # ):
    #     """
    #     Apply the spin outcome to the wallet.
 
    #     Routing rules:
    #         Source = deposit_coins:
    #           Win / Partial → credit EARNINGS at 100%
    #           Push          → return stake to deposit_coins
    #           Loss          → stake gone
 
    #         Source = bonus_coins:
    #           Win / Partial → credit EARNINGS at BONUS_WALLET_PAYOUT_RATE (40%)
    #           Push          → return stake to bonus_coins
    #           Loss          → stake gone
 
    #         Welcome spin (no source): credit EARNINGS directly at 100%.
    #     """
    #     # Apply the bonus payout rate if source is bonus_coins
    #     if source_wallet == 'bonus_coins':
    #         payout_rate = get_setting(SettingKey.BONUS_WALLET_PAYOUT_RATE)
    #     else:
    #         payout_rate = Decimal('1')
 
    #     # ─── Welcome spin: no lock to release. Just credit EARNINGS on win. ───
    #     if is_welcome:
    #         if payout_amount > 0:
    #             # Welcome wins always go to earnings at 100% (no source wallet)
    #             tx = WalletService.credit(
    #                 user=user,
    #                 amount=payout_amount,
    #                 balance_type=Transaction.BalanceType.NAIRA_WITHDRAW,
    #                 tx_type=Transaction.Type.WIN,
    #                 reference_id=f'{spin_reference}:welcome_payout',
    #                 metadata={'wheel': wheel.wheel_type, 'welcome': True},
    #             )
    #             return Spin.Outcome.WIN, tx
    #         return Spin.Outcome.LOSS, None
 
    #     # ─── Loss (mult = 0): forfeit stake ───
    #     if multiplier == 0:
    #         forfeit_tx = WalletService.forfeit(
    #             user=user,
    #             lock_reference_id=spin_reference,
    #             resolve_reference_id=f'{spin_reference}:resolve',
    #             metadata={
    #                 'wheel': wheel.wheel_type,
    #                 'source_wallet': source_wallet,
    #                 'multiplier': '0',
    #             },
    #         )
    #         return Spin.Outcome.LOSS, forfeit_tx
 
    #     # ─── Push (mult = 1): stake returned to ORIGINAL source wallet ───
    #     if multiplier == Decimal('1'):
    #         # First: forfeit removes stake from STAKED pool
    #         WalletService.forfeit(
    #             user=user,
    #             lock_reference_id=spin_reference,
    #             resolve_reference_id=f'{spin_reference}:push_forfeit',
    #             metadata={
    #                 'wheel': wheel.wheel_type,
    #                 'source_wallet': source_wallet,
    #                 'multiplier': '1',
    #                 'note': 'push — refunding stake to source wallet',
    #             },
    #         )
    #         # Then: credit stake back to the source wallet
    #         refund_tx = WalletService.credit(
    #             user=user,
    #             amount=stake_amount,
    #             balance_type=source_balance_type,
    #             tx_type=Transaction.Type.WIN,
    #             reference_id=f'{spin_reference}:push_refund',
    #             metadata={
    #                 'wheel': wheel.wheel_type,
    #                 'source_wallet': source_wallet,
    #                 'note': 'push refund',
    #             },
    #         )
    #         return Spin.Outcome.PUSH, refund_tx
 
    #     # ─── Partial loss (0 < mult < 1): forfeit stake, credit partial to EARNINGS ───
    #     if multiplier < Decimal('1'):
    #         WalletService.forfeit(
    #             user=user,
    #             lock_reference_id=spin_reference,
    #             resolve_reference_id=f'{spin_reference}:partial_forfeit',
    #             metadata={
    #                 'wheel': wheel.wheel_type,
    #                 'source_wallet': source_wallet,
    #                 'multiplier': str(multiplier),
    #             },
    #         )
    #         # payout_amount = stake × mult. Apply 40% if bonus.
    #         credited = (payout_amount * payout_rate).quantize(
    #             Decimal('0.01'), rounding=ROUND_HALF_UP,
    #         )
    #         partial_tx = WalletService.credit(
    #             user=user,
    #             amount=credited,
    #             balance_type=Transaction.BalanceType.NAIRA_WITHDRAW,
    #             tx_type=Transaction.Type.WIN,
    #             reference_id=f'{spin_reference}:partial_credit',
    #             metadata={
    #                 'wheel': wheel.wheel_type,
    #                 'source_wallet': source_wallet,
    #                 'multiplier': str(multiplier),
    #                 'payout_rate': str(payout_rate),
    #                 'gross_payout': str(payout_amount),
    #                 'net_credited': str(credited),
    #                 'note': 'partial_loss_payout',
    #             },
    #         )
    #         return Spin.Outcome.PARTIAL_LOSS, partial_tx
 
    #     # ─── Win (mult > 1) ───
    #     # Forfeit stake from STAKED pool (the stake doesn't return — it's "consumed")
    #     # Then credit (stake + winnings) × payout_rate to EARNINGS.
    #     # For deposit: rate=1 → user effectively gets stake back + winnings.
    #     # For bonus:   rate=0.4 → user gets 40% of (stake+winnings) to earnings.
    #     WalletService.forfeit(
    #         user=user,
    #         lock_reference_id=spin_reference,
    #         resolve_reference_id=f'{spin_reference}:win_forfeit',
    #         metadata={
    #             'wheel': wheel.wheel_type,
    #             'source_wallet': source_wallet,
    #             'multiplier': str(multiplier),
    #             'note': 'win — clearing staked pool before crediting earnings',
    #         },
    #     )
 
    #     gross_payout = payout_amount  # = stake × multiplier
    #     credited = (gross_payout * payout_rate).quantize(
    #         Decimal('0.01'), rounding=ROUND_HALF_UP,
    #     )
 
    #     win_tx = WalletService.credit(
    #         user=user,
    #         amount=credited,
    #         balance_type=Transaction.BalanceType.NAIRA_WITHDRAW,
    #         tx_type=Transaction.Type.WIN,
    #         reference_id=f'{spin_reference}:win_credit',
    #         metadata={
    #             'wheel': wheel.wheel_type,
    #             'source_wallet': source_wallet,
    #             'multiplier': str(multiplier),
    #             'payout_rate': str(payout_rate),
    #             'gross_payout': str(gross_payout),
    #             'net_credited': str(credited),
    #         },
    #     )
    #     MIN_NOTIFY_AMOUNT = Decimal('1000')
    #     if credited >= MIN_NOTIFY_AMOUNT:
    #         NotificationService.send_async(
    #             telegram_id=user.telegram_id,
    #             notification_type='spin_win',
    #             data={'amount': str(credited)},
    #         )
    #     return Spin.Outcome.WIN, win_tx
    @staticmethod
    def _resolve(
        user,
        wheel,
        spin_reference: str,
        stake_amount: Decimal,
        multiplier: Decimal,
        payout_amount: Decimal,
        source_wallet: str,
        source_balance_type: str,
        bonus_destination: str,
    ):
        """
        Resolve the spin outcome with currency-aware routing.
 
        Returns (outcome, resolution_tx, resolution_meta) where meta has:
            payout_currency:   NGN | USDT | '' (loss)
            credited_balance:  destination balance_type string | '' (loss)
 
        ROUTING:
            CRYPTO_COINS source → wins land in CRYPTO_WITHDRAW at 100% of payout
            NAIRA_COINS source  → wins land in NAIRA_WITHDRAW at 100% of payout
            BONUS_COINS source  → wins land in chosen rail at
                                  payout × BONUS_PAYOUT_RATE × BONUS_TO_{NGN|USDT}_RATE
 
        OUTCOMES:
            multiplier = 0  → loss (forfeit stake, no credit)
            multiplier = 1  → push (stake returned to source bucket)
            0 < mult < 1    → partial (forfeit stake, credit partial)
            mult > 1        → win (forfeit stake, credit win)
        """
        # ─── LOSS (multiplier = 0) ───────────────────────────────────────
        if multiplier == Decimal('0'):
            forfeit_tx = WalletService.forfeit(
                user=user,
                lock_reference_id=spin_reference,
                resolve_reference_id=f'{spin_reference}:loss_forfeit',
                metadata={
                    'wheel': wheel.wheel_type,
                    'source_wallet': source_wallet,
                    'multiplier': '0',
                },
            )
            return Spin.Outcome.LOSS, forfeit_tx, {
                'payout_currency': '',
                'credited_balance': '',
                'net_credited': Decimal('0'),
            }
 
        # ─── PUSH (multiplier = 1) — refund stake to origin bucket ───────
        if multiplier == Decimal('1'):
            WalletService.forfeit(
                user=user,
                lock_reference_id=spin_reference,
                resolve_reference_id=f'{spin_reference}:push_forfeit',
                metadata={
                    'wheel': wheel.wheel_type,
                    'source_wallet': source_wallet,
                    'multiplier': '1',
                    'note': 'push — refunding stake to source wallet',
                },
            )
            refund_tx = WalletService.credit(
                user=user,
                amount=stake_amount,
                balance_type=source_balance_type,
                tx_type=Transaction.Type.WIN,
                reference_id=f'{spin_reference}:push_refund',
                metadata={
                    'wheel': wheel.wheel_type,
                    'source_wallet': source_wallet,
                    'note': 'push refund to origin',
                },
            )
            return Spin.Outcome.PUSH, refund_tx, {
                'payout_currency': '',  # not a real win
                'credited_balance': source_balance_type,
                'net_credited': stake_amount,
            }
 
        # ─── WIN OR PARTIAL — compute destination + credited amount ──────
        # Forfeit stake from STAKED pool first (stake is consumed on win)
        WalletService.forfeit(
            user=user,
            lock_reference_id=spin_reference,
            resolve_reference_id=f'{spin_reference}:resolve_forfeit',
            metadata={
                'wheel': wheel.wheel_type,
                'source_wallet': source_wallet,
                'multiplier': str(multiplier),
            },
        )
 
        # Determine destination + apply bonus conversion if needed
        if source_wallet == 'bonus_coins':
            # Bonus: apply payout_rate × bonus_to_{currency}_rate
            bonus_rate = get_setting(SettingKey.BONUS_PAYOUT_RATE)
            conversion_rate = get_setting(
                BONUS_DESTINATION_TO_RATE_SETTING[bonus_destination]
            )
            credited_amount = (
                payout_amount * bonus_rate * conversion_rate
            ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
 
            destination_balance_type = BONUS_DESTINATION_TO_BALANCE_TYPE[bonus_destination]
            payout_currency = BONUS_DESTINATION_TO_CURRENCY[bonus_destination]
 
            credit_metadata = {
                'wheel': wheel.wheel_type,
                'source_wallet': 'bonus_coins',
                'bonus_destination': bonus_destination,
                'gross_payout': str(payout_amount),
                'bonus_payout_rate': str(bonus_rate),
                'conversion_rate': str(conversion_rate),
                'net_credited': str(credited_amount),
            }
        else:
            # Crypto or Naira source: 100% to matching withdraw balance
            credited_amount = payout_amount  # gross = net (no rate applied)
            destination_balance_type = SOURCE_WALLET_TO_WIN_DESTINATION[source_wallet]
            payout_currency = SOURCE_WALLET_TO_CURRENCY[source_wallet]
 
            credit_metadata = {
                'wheel': wheel.wheel_type,
                'source_wallet': source_wallet,
                'multiplier': str(multiplier),
                'gross_payout': str(payout_amount),
                'net_credited': str(credited_amount),
            }
 
        # Credit the win/partial amount
        result_tx = WalletService.credit(
            user=user,
            amount=credited_amount,
            balance_type=destination_balance_type,
            tx_type=Transaction.Type.WIN,
            reference_id=f'{spin_reference}:credit',
            metadata=credit_metadata,
        )
 
        # Determine outcome
        if multiplier < Decimal('1'):
            outcome = Spin.Outcome.PARTIAL_LOSS
        else:
            outcome = Spin.Outcome.WIN
 
        # ─── Optional: notify on big wins ────────────────────────────────
        try:
            from apps.notifications.services import NotificationService
            MIN_NOTIFY = Decimal('1000') if payout_currency == 'NGN' else Decimal('5')
            if credited_amount >= MIN_NOTIFY:
                NotificationService.send_async(
                    telegram_id=user.telegram_id,
                    notification_type='spin_win',
                    data={
                        'amount': str(credited_amount),
                        'currency': payout_currency,
                    },
                )
        except ImportError:
            pass  # notifications app not installed
 
        return outcome, result_tx, {
            'payout_currency': payout_currency,
            'credited_balance': destination_balance_type,
            'net_credited': credited_amount,
        }