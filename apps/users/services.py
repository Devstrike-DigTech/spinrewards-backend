import json
import logging
from django.db import transaction as db_transaction
from rest_framework_simplejwt.tokens import RefreshToken

from common.utils import validate_telegram_init_data
from common.exceptions import InvalidTelegramAuthError
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
        try:
            parsed = validate_telegram_init_data(init_data)
        except ValueError as e:
            raise InvalidTelegramAuthError(str(e))

        # Parse the user object from initData
        user_data_str = parsed.get('user', '{}')
        try:
            user_data = json.loads(user_data_str)
        except json.JSONDecodeError:
            raise InvalidTelegramAuthError('Malformed user data in initData.')

        telegram_id = str(user_data.get('id', ''))
        if not telegram_id:
            raise InvalidTelegramAuthError('No user ID in initData.')

        with db_transaction.atomic():
            user, created = User.objects.get_or_create(
                telegram_id=telegram_id,
                defaults={
                    'username': user_data.get('username', ''),
                    'first_name': user_data.get('first_name', ''),
                    'last_name': user_data.get('last_name', ''),
                }
            )

            if not created:
                # Update name fields in case they changed on Telegram
                updated_fields = []
                for field in ('username', 'first_name', 'last_name'):
                    new_val = user_data.get(field, '')
                    if getattr(user, field) != new_val and new_val:
                        setattr(user, field, new_val)
                        updated_fields.append(field)
                if updated_fields:
                    user.save(update_fields=updated_fields + ['updated_at'])

            if created:
                # Create wallet on first registration
                from apps.wallet.models import Wallet
                Wallet.objects.create(user=user)
                logger.info('New user registered: %s', telegram_id)

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
        from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
        try:
            refresh = RefreshToken(refresh_token)
            return {'access': str(refresh.access_token)}
        except (TokenError, InvalidToken) as e:
            from common.exceptions import SpinRewardsException
            raise SpinRewardsException(str(e))
