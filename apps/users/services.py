# import json
# import logging
# from django.db import transaction as db_transaction
# from rest_framework_simplejwt.tokens import RefreshToken

# from common.utils import validate_telegram_init_data
# from common.exceptions import InvalidTelegramAuthError
# from .models import User

# logger = logging.getLogger(__name__)


# class AuthService:
#     @staticmethod
#     def authenticate_telegram(init_data: str) -> tuple[User, dict]:
#         """
#         Validate Telegram initData, create or retrieve user, return JWT tokens.

#         Returns:
#             (user, tokens) where tokens = {'access': '...', 'refresh': '...'}
#         """
#         try:
#             parsed = validate_telegram_init_data(init_data)
#         except ValueError as e:
#             logger.warning('Telegram auth rejected: %s', e)
#             raise InvalidTelegramAuthError(str(e))

#         # Parse the user object from initData
#         user_data_str = parsed.get('user', '{}')
#         try:
#             user_data = json.loads(user_data_str)
#         except json.JSONDecodeError:
#             logger.warning('Telegram auth rejected: malformed user JSON')
#             raise InvalidTelegramAuthError('Malformed user data in initData.')

#         telegram_id = user_data.get('id')
#         if not telegram_id:
#             raise InvalidTelegramAuthError('No user ID in initData.')

#         with db_transaction.atomic():
#             user, created = User.objects.get_or_create(
#                 telegram_id=telegram_id,
#                 defaults={
#                     'username': user_data.get('username', ''),
#                     'first_name': user_data.get('first_name', ''),
#                     'last_name': user_data.get('last_name', ''),
#                 }
#             )

#             if not created:
#                 # Update name fields in case they changed on Telegram
#                 updated_fields = []
#                 for field in ('username', 'first_name', 'last_name'):
#                     new_val = user_data.get(field, '')
#                     if getattr(user, field) != new_val and new_val:
#                         setattr(user, field, new_val)
#                         updated_fields.append(field)
#                 if updated_fields:
#                     user.save(update_fields=updated_fields + ['updated_at'])

#             if created:
#                 # Create wallet on first registration
#                 from apps.wallet.models import Wallet
#                 Wallet.objects.create(user=user)
#                 logger.info('New user registered: %s', telegram_id)

#         tokens = AuthService._generate_tokens(user)
#         return user, tokens

#     @staticmethod
#     def _generate_tokens(user: User) -> dict:
#         refresh = RefreshToken.for_user(user)
#         return {
#             'access': str(refresh.access_token),
#             'refresh': str(refresh),
#         }

#     @staticmethod
#     def refresh_tokens(refresh_token: str) -> dict:
#         from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
#         try:
#             refresh = RefreshToken(refresh_token)
#             return {'access': str(refresh.access_token)}
#         except (TokenError, InvalidToken) as e:
#             from common.exceptions import SpinRewardsException
#             raise SpinRewardsException(str(e))
import json
import logging

from django.db import transaction as db_transaction
from django.db import IntegrityError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken

from common.utils import validate_telegram_init_data
from common.exceptions import InvalidTelegramAuthError, SpinRewardsException
from .models import User

logger = logging.getLogger(__name__)


class AuthService:
    @staticmethod
    def authenticate_telegram(init_data: str) -> tuple[User, dict]:
        """
        Validate Telegram initData, create or retrieve user, return JWT tokens.

        Returns:
            (user, tokens) where tokens = {'access': '...', 'refresh': '...'}
        """
        # ── Validate initData ──────────────────────────────────────────────
        # We log the specific reason for observability but return a generic
        # message to the client. Attackers must not learn whether they got the
        # signature right vs some other field wrong.
        try:
            parsed = validate_telegram_init_data(init_data)
        except ValueError as e:
            logger.warning('Telegram auth rejected: %s', e)
            raise InvalidTelegramAuthError('Telegram authentication failed.')

        # ── Parse the Telegram user object ─────────────────────────────────
        user_data_str = parsed.get('user', '{}')
        try:
            user_data = json.loads(user_data_str)
        except json.JSONDecodeError:
            logger.warning('Telegram auth rejected: malformed user JSON')
            raise InvalidTelegramAuthError('Telegram authentication failed.')

        telegram_id = user_data.get('id')
        if not isinstance(telegram_id, int) or telegram_id <= 0:
            logger.warning('Telegram auth rejected: missing or invalid user id')
            raise InvalidTelegramAuthError('Telegram authentication failed.')

        # ── Get-or-create user (race-safe) ─────────────────────────────────
        with db_transaction.atomic():
            try:
                user, created = User.objects.get_or_create(
                    telegram_id=telegram_id,
                    defaults={
                        'username': user_data.get('username', '') or '',
                        'first_name': user_data.get('first_name', '') or '',
                        'last_name': user_data.get('last_name', '') or '',
                    }
                )
            except IntegrityError:
                # Lost the race: another concurrent request created this user
                # between our get() and create(). Just fetch the winning row.
                user = User.objects.get(telegram_id=telegram_id)
                created = False

            if created:
                logger.info('New user registered: telegram_id=%s', telegram_id)
            else:
                # Sync name fields if Telegram sent updated values.
                # We only overwrite with NON-EMPTY values so a missing field
                # in a later payload doesn't wipe the user's stored data.
                updated_fields = []
                for field in ('username', 'first_name', 'last_name'):
                    new_val = user_data.get(field, '') or ''
                    if new_val and getattr(user, field) != new_val:
                        setattr(user, field, new_val)
                        updated_fields.append(field)
                if updated_fields:
                    user.save(update_fields=updated_fields + ['updated_at'])

        # Wallet creation is handled by a post_save signal on User.
        # See apps/users/signals.py — the signal runs on commit of the
        # transaction above, so by the time we issue tokens, the wallet exists.

        tokens = AuthService._generate_tokens(user)
        return user, tokens

    @staticmethod
    def _generate_tokens(user: User) -> dict:
        refresh = RefreshToken.for_user(user)
        return {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }

    @staticmethod
    def refresh_tokens(refresh_token: str) -> dict:
        """
        Issue a new access token from a valid refresh token.

        simplejwt's built-in TokenRefreshView handles rotation + blacklisting
        natively. If you want that, swap the view wiring in urls.py. This
        method exists for the manual flow currently in TokenRefreshView.
        """
        try:
            refresh = RefreshToken(refresh_token)
            return {'access': str(refresh.access_token)}
        except (TokenError, InvalidToken) as e:
            logger.info('Token refresh rejected: %s', e)
            raise SpinRewardsException('Invalid or expired refresh token.')