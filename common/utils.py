import uuid
import hashlib
import hmac
import secrets
import string
from urllib.parse import parse_qsl

from cryptography.fernet import Fernet
from django.conf import settings


# ─── Encryption ───────────────────────────────────────────────────────────────

def _get_fernet() -> Fernet:
    return Fernet(settings.ENCRYPTION_KEY.encode())


def encrypt(value: str) -> str:
    """Encrypt a string value. Used for NIN and other PII."""
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Decrypt an encrypted string value."""
    return _get_fernet().decrypt(value.encode()).decode()


# ─── Reference Generation ─────────────────────────────────────────────────────

def generate_reference(prefix: str = '') -> str:
    """
    Generate a unique alphanumeric reference ID.
    Example: WD-A1B2C3D4E5F6
    """
    chars = string.ascii_uppercase + string.digits
    suffix = ''.join(secrets.choice(chars) for _ in range(12))
    return f"{prefix}-{suffix}" if prefix else suffix


def generate_idempotency_key() -> str:
    return uuid.uuid4().hex


# ─── Telegram initData Validation ─────────────────────────────────────────────

def validate_telegram_init_data(init_data: str) -> dict:
    """
    Validate Telegram WebApp initData HMAC signature.
    Returns parsed user data dict if valid.
    Raises ValueError if invalid.

    Reference: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    bot_token = settings.TELEGRAM_BOT_TOKEN
    parsed = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = parsed.pop('hash', None)

    if not received_hash:
        raise ValueError('Missing hash in initData')

    data_check_string = '\n'.join(
        f'{k}={v}' for k, v in sorted(parsed.items())
    )

    secret_key = hmac.new(
        b'WebAppData',
        bot_token.encode(),
        hashlib.sha256,
    ).digest()

    computed_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        raise ValueError('Invalid Telegram signature')

    return parsed


# ─── Payment Signature Verification ──────────────────────────────────────────

def verify_paystack_signature(payload_bytes: bytes, signature: str) -> bool:
    """Verify Paystack webhook HMAC-SHA512 signature."""
    secret_key = settings.PAYSTACK_SECRET_KEY
    expected = hmac.new(
        secret_key.encode(),
        payload_bytes,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_flutterwave_signature(signature: str) -> bool:
    """Verify Flutterwave webhook secret hash."""
    return hmac.compare_digest(signature, settings.FLUTTERWAVE_SECRET_HASH)
