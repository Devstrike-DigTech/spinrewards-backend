import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    PermissionDenied,
    ValidationError,
    NotFound,
    Throttled,
)

logger = logging.getLogger(__name__)


# ─── Custom Exception Classes ─────────────────────────────────────────────────

class SpinRewardsException(Exception):
    """Base exception for all application errors."""
    code = 'SERVER_ERROR'
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_message = 'An unexpected error occurred.'

    def __init__(self, message=None):
        self.message = message or self.default_message
        super().__init__(self.message)


class InsufficientFundsError(SpinRewardsException):
    code = 'INSUFFICIENT_FUNDS'
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = 'Insufficient balance to complete this action.'


class InvalidStakeError(SpinRewardsException):
    code = 'INVALID_STAKE'
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = 'Stake amount does not match any active tier.'


class DuplicateRequestError(SpinRewardsException):
    code = 'DUPLICATE_REQUEST'
    status_code = status.HTTP_409_CONFLICT
    default_message = 'This request has already been processed.'


class KYCRequiredError(SpinRewardsException):
    code = 'KYC_REQUIRED'
    status_code = status.HTTP_403_FORBIDDEN
    default_message = 'KYC approval is required to perform this action.'


class InvalidTelegramAuthError(SpinRewardsException):
    code = 'INVALID_TELEGRAM_AUTH'
    status_code = status.HTTP_401_UNAUTHORIZED
    default_message = 'Telegram authentication failed.'


class ConfigurationError(SpinRewardsException):
    code = 'CONFIGURATION_ERROR'
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_message = 'System configuration error. Please contact support.'


class InvalidProbabilityError(SpinRewardsException):
    code = 'INVALID_PROBABILITY'
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = 'Outcome probabilities must sum to exactly 100%.'


class BankVerificationError(SpinRewardsException):
    code = 'BANK_VERIFICATION_FAILED'
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = 'Could not verify the provided bank account.'


class ProviderError(SpinRewardsException):
    code = 'PROVIDER_ERROR'
    status_code = status.HTTP_502_BAD_GATEWAY
    default_message = 'Payment provider is currently unavailable.'


class RewardAlreadyClaimedError(SpinRewardsException):
    code = 'REWARD_ALREADY_CLAIMED'
    status_code = status.HTTP_400_BAD_REQUEST
    default_message = 'Daily reward has already been claimed today.'


# ─── DRF Exception Handler ────────────────────────────────────────────────────

def custom_exception_handler(exc, context):
    """
    Custom DRF exception handler.
    Returns a consistent error envelope:
    { "error": true, "code": "...", "message": "..." }
    """
    # Let DRF handle its own exceptions first
    response = exception_handler(exc, context)

    if isinstance(exc, SpinRewardsException):
        return Response(
            {
                'error': True,
                'code': exc.code,
                'message': exc.message,
            },
            status=exc.status_code,
        )

    if response is not None:
        code, message = _map_drf_exception(exc, response)
        fields = None

        if isinstance(exc, ValidationError):
            fields = response.data if isinstance(response.data, dict) else None
            message = 'Validation failed.'

        payload = {
            'error': True,
            'code': code,
            'message': message,
        }
        if fields:
            payload['fields'] = fields

        response.data = payload
        return response

    # Unhandled exception — log it and return 500
    logger.exception('Unhandled exception: %s', exc)
    return Response(
        {
            'error': True,
            'code': 'SERVER_ERROR',
            'message': 'An unexpected error occurred.',
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _map_drf_exception(exc, response):
    mapping = {
        NotAuthenticated: ('TOKEN_EXPIRED', 'Authentication credentials were not provided.'),
        AuthenticationFailed: ('INVALID_TOKEN', 'Invalid or expired authentication token.'),
        PermissionDenied: ('UNAUTHORIZED', 'You do not have permission to perform this action.'),
        NotFound: ('NOT_FOUND', 'The requested resource was not found.'),
        Throttled: ('RATE_LIMITED', 'Too many requests. Please try again later.'),
        ValidationError: ('VALIDATION_ERROR', 'Validation failed.'),
    }
    for exc_type, (code, message) in mapping.items():
        if isinstance(exc, exc_type):
            return code, message
    return 'SERVER_ERROR', str(response.data)
