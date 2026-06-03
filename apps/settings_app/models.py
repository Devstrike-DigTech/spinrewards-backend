"""
SystemSetting model — admin-controlled key/value config.

Used for values that the admin needs to change at runtime without a deploy:
  - Conversion rates (coins per NGN, coins per USD)
  - Min deposit amounts (NGN, USD)
  - Bonus wallet payout rate (40%)
  - Display rate for USD equivalent on earnings

Each setting has a default that comes from environment variables. If the DB
row exists, it overrides the env. If neither exists, the code uses a
hard-coded default.

The service layer (services.py) handles cache invalidation, so callers can
read settings repeatedly without DB hits.
"""
import uuid

from django.db import models


class SystemSetting(models.Model):
    """
    Admin-editable configuration value.

    `value` is stored as a Decimal — works for rates, amounts, and integer
    counts alike. If a setting is conceptually boolean or string, store it
    as 0/1 or use a different model.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=80, unique=True, db_index=True)
    value = models.DecimalField(max_digits=20, decimal_places=8)
    description = models.CharField(
        max_length=255, blank=True,
        help_text='Human-readable hint shown in admin UI.',
    )
    updated_by = models.ForeignKey(
        'users.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'system_settings'
        ordering = ['key']

    def __str__(self):
        return f'{self.key} = {self.value}'


# ─── Known setting keys (single source of truth) ────────────────────────────────

class SettingKey:
    """All known setting keys, with their env-var defaults and descriptions."""

    COINS_PER_NGN = 'COINS_PER_NGN'
    COINS_PER_USD = 'COINS_PER_USD'
    BONUS_WALLET_PAYOUT_RATE = 'BONUS_WALLET_PAYOUT_RATE'
    MIN_DEPOSIT_NGN = 'MIN_DEPOSIT_NGN'
    MIN_DEPOSIT_USD = 'MIN_DEPOSIT_USD'
    NGN_PER_USD_DISPLAY_RATE = 'NGN_PER_USD_DISPLAY_RATE'

    # Map of key → (default, description)
    DEFAULTS = {
        COINS_PER_NGN: (
            '1',
            'How many coins are credited per ₦1 deposited via Paystack.',
        ),
        COINS_PER_USD: (
            '1500',
            'How many coins are credited per $1 of crypto deposited via NowPayments.',
        ),
        BONUS_WALLET_PAYOUT_RATE: (
            '0.40',
            'Fraction of bonus-coin spin winnings credited to earnings (rest evaporates).',
        ),
        MIN_DEPOSIT_NGN: (
            '1000',
            'Minimum naira deposit amount.',
        ),
        MIN_DEPOSIT_USD: (
            '20',
            'Minimum USD-equivalent crypto deposit.',
        ),
        NGN_PER_USD_DISPLAY_RATE: (
            '1500',
            'Rate used to display USD equivalent of NGN earnings (display only — '
            'not used for transactions).',
        ),
    }

    @classmethod
    def all_keys(cls):
        return list(cls.DEFAULTS.keys())
