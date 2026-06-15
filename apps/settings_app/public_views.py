"""
Public settings endpoint — exposes non-sensitive config values to the frontend.

Goal: frontend can fetch the current min deposit amounts and conversion rates
dynamically, so admin changes take effect without a frontend redeploy.

Endpoint:
    GET /api/v1/settings/public/

No authentication required. Cached client-side recommended (1 minute).

ADD this view to apps/settings_app/views.py (or as a new file public_views.py).
"""
from decimal import Decimal

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SettingKey
from .services import get_setting


class PublicSettingsView(APIView):
    """
    GET /api/v1/settings/public/

    Returns frontend-facing config values. No auth required.
    Frontend should fetch once per session and use to populate
    min-deposit warnings, "you'll get X coins" previews, etc.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({
            'success': True,
            'data': {
                # ─── Bonus economy ─────────────────────────────────────
                'bonus_payout_rate': str(get_setting(SettingKey.BONUS_PAYOUT_RATE)),
                'bonus_to_ngn_rate': str(get_setting(SettingKey.BONUS_TO_NGN_RATE)),
                'bonus_to_usdt_rate': str(get_setting(SettingKey.BONUS_TO_USDT_RATE)),
        
                # ─── Display rate (NOT used for any math — display only) ──
                'ngn_per_usd_display_rate': str(get_setting(SettingKey.NGN_PER_USD_DISPLAY_RATE)),
        
                # ─── Deposit minimums ──────────────────────────────────
                'min_deposit_ngn': str(get_setting(SettingKey.MIN_DEPOSIT_NGN)),
                'min_deposit_usd': str(get_setting(SettingKey.MIN_DEPOSIT_USD)),
        
                # ─── Withdrawal minimums ───────────────────────────────
                'min_withdrawal_ngn': str(get_setting(SettingKey.MIN_WITHDRAWAL_NGN)),
                'min_withdrawal_usdt': str(get_setting(SettingKey.MIN_WITHDRAWAL_USDT)),
        
                # ─── Feature flags ─────────────────────────────────────
                'crypto_withdrawal_enabled': bool(
                    get_setting(SettingKey.CRYPTO_WITHDRAWAL_ENABLED) > 0
                ),
            },
        })
        # return Response({
        #     'success': True,
        #     'data': {
        #         # Deposit minimums (used in deposit form validation)
        #         'min_deposit_ngn': str(get_setting(SettingKey.MIN_DEPOSIT_NGN)),
        #         'min_deposit_usd': str(get_setting(SettingKey.MIN_DEPOSIT_USD)),

        #         # Conversion rates (used in "you'll get X coins" previews)
        #         'coins_per_ngn': str(get_setting(SettingKey.COINS_PER_NGN)),
        #         'coins_per_usd': str(get_setting(SettingKey.COINS_PER_USD)),

        #         # Display rate for USD equivalent of earnings
        #         'ngn_per_usd_display_rate': str(get_setting(SettingKey.NGN_PER_USD_DISPLAY_RATE)),

        #         # Bonus wallet rate (shown on spin source picker UI)
        #         'bonus_wallet_payout_rate': str(get_setting(SettingKey.BONUS_WALLET_PAYOUT_RATE)),
        #     },
        # })