"""
Referral service.

All referral business logic lives here. Views and signals call into this.

Key methods:
  ReferralService.get_or_create_code(user)      → ReferralCode
  ReferralService.apply_code(referred_user, code_str) → Referral
  ReferralService.qualify(referred_user)        → called after first deposit
"""
import logging
import secrets
import string

from django.db import transaction
from django.utils import timezone

from .models import Referral, ReferralCode

logger = logging.getLogger(__name__)

CODE_PREFIX  = 'SPIN'
CODE_LENGTH  = 8   # characters after the prefix
CODE_CHARS   = string.ascii_uppercase + string.digits


class ReferralServiceError(Exception):
    pass


class ReferralService:

    # ── Code management ───────────────────────────────────────────────────────

    @classmethod
    def get_or_create_code(cls, user) -> ReferralCode:
        """
        Return the user's referral code, creating one if it doesn't exist.
        Idempotent — safe to call multiple times.
        """
        try:
            return user.my_referral_code
        except ReferralCode.DoesNotExist:
            pass

        code_str = cls._generate_unique_code()
        code = ReferralCode.objects.create(user=user, code=code_str)
        logger.info('Referral code created: %s for user=%s', code_str, user.id)
        return code

    @classmethod
    def _generate_unique_code(cls) -> str:
        """Generate a unique referral code like SPIN-X7K2M9PQ."""
        for _ in range(10):  # retry up to 10 times on collision
            suffix = ''.join(secrets.choice(CODE_CHARS) for _ in range(CODE_LENGTH))
            code = f'{CODE_PREFIX}-{suffix}'
            if not ReferralCode.objects.filter(code=code).exists():
                return code
        raise ReferralServiceError('Failed to generate unique referral code after 10 attempts.')

    # ── Applying a code ───────────────────────────────────────────────────────

    @classmethod
    def apply_code(cls, referred_user, code_str: str) -> Referral:
        """
        Apply a referral code for a newly registered user.

        Rules:
          1. Code must exist and be active
          2. Referred user cannot refer themselves
          3. Referred user cannot already have a referral entry
          4. Referred user must not already have made a deposit (new user only)

        Returns the created Referral (status=pending).
        Raises ReferralServiceError on any validation failure.
        """
        code_str = code_str.strip().upper()

        # Look up the code
        try:
            referral_code = ReferralCode.objects.select_related('user').get(
                code=code_str,
                is_active=True,
            )
        except ReferralCode.DoesNotExist:
            raise ReferralServiceError(f'Referral code "{code_str}" is invalid or inactive.')

        referrer = referral_code.user

        # Self-referral check
        if referrer.id == referred_user.id:
            raise ReferralServiceError('You cannot use your own referral code.')

        # Duplicate check
        if Referral.objects.filter(referred_user=referred_user).exists():
            raise ReferralServiceError('You have already used a referral code.')

        # New user check — must not have deposited before applying code
        try:
            from apps.wallet.models import Transaction
            has_deposits = Transaction.objects.filter(
                user=referred_user,
                type='deposit',
                status='completed',
            ).exists()
            if has_deposits:
                raise ReferralServiceError(
                    'Referral codes can only be applied by new users before their first deposit.'
                )
        except ImportError:
            pass

        with transaction.atomic():
            referral = Referral.objects.create(
                referrer=referrer,
                referred_user=referred_user,
                referral_code=referral_code,
                status=Referral.Status.PENDING,
            )

        logger.info(
            'Referral applied: referrer=%s referred_user=%s code=%s',
            referrer.id, referred_user.id, code_str,
        )
        return referral

    # ── Qualification ─────────────────────────────────────────────────────────

    @classmethod
    def qualify(cls, referred_user) -> Referral | None:
        """
        Called after the referred user completes their first deposit.

        Marks the referral as qualified, then fires the challenge signal
        so the referrer's challenge progress gets incremented.

        Returns the Referral if found, None if the user has no referral entry.
        """
        try:
            referral = Referral.objects.select_related(
                'referrer', 'referred_user', 'referral_code'
            ).get(
                referred_user=referred_user,
                status=Referral.Status.PENDING,
            )
        except Referral.DoesNotExist:
            return None

        with transaction.atomic():
            referral.status      = Referral.Status.QUALIFIED
            referral.qualified_at = timezone.now()
            referral.save(update_fields=['status', 'qualified_at', 'updated_at'])

        logger.info(
            'Referral qualified: referrer=%s referred_user=%s',
            referral.referrer_id, referred_user.id,
        )

        # Fire challenge signal so referrer's challenge progress is incremented
        try:
            from apps.challenges.signals import referral_qualified
            referral_qualified.send(
                sender=cls,
                referrer=referral.referrer,
                referred_user=referred_user,
            )
        except Exception as e:
            logger.exception('Failed to fire referral_qualified signal: %s', e)

        return referral

    @classmethod
    def mark_rewarded(cls, referral: Referral, reward_snapshot: dict = None):
        """
        Mark a referral as rewarded after the challenge engine distributes the reward.
        Called from the challenge engine reward distribution step.
        """
        referral.status          = Referral.Status.REWARDED
        referral.rewarded_at     = timezone.now()
        referral.reward_snapshot = reward_snapshot or {}
        referral.save(update_fields=['status', 'rewarded_at', 'reward_snapshot', 'updated_at'])
        logger.info('Referral rewarded: %s', referral.id)

    # ── Read helpers ──────────────────────────────────────────────────────────

    @classmethod
    def get_referral_stats(cls, user) -> dict:
        """Summary stats for a user's referral activity."""
        qs = Referral.objects.filter(referrer=user)
        return {
            'total_referrals': qs.count(),
            'pending':   qs.filter(status=Referral.Status.PENDING).count(),
            'qualified': qs.filter(status=Referral.Status.QUALIFIED).count(),
            'rewarded':  qs.filter(status=Referral.Status.REWARDED).count(),
        }