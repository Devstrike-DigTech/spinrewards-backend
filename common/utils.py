import uuid
import hashlib
import time
import hmac
import secrets
import string
from urllib.parse import unquote
from urllib.parse import parse_qsl

from cryptography.fernet import Fernet
from django.conf import settings

TELEGRAM_AUTH_MAX_AGE_SECONDS = 300 
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
    Validate Telegram WebApp initData HMAC signature and freshness.
 
    Returns parsed key/value dict if valid.
    Raises ValueError if invalid.
 
    Reference: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
 
    Security notes:
      - We parse the query string manually (not parse_qsl) to avoid any
        URL-decoding ambiguity that could break HMAC comparison.
      - auth_date is checked to prevent replay attacks using captured initData.
      - Empty bot token fails loudly — a missing env var must NEVER silently
        produce a "valid" HMAC.
    """
    bot_token = settings.TELEGRAM_BOT_TOKEN
    if not bot_token:
        raise ValueError('TELEGRAM_BOT_TOKEN not configured')
 
    if not init_data or not isinstance(init_data, str):
        raise ValueError('Malformed initData')
 
    # Split manually — do NOT use parse_qsl with strict_parsing=True here,
    # as it rejects legitimate payloads and its URL-decoding behavior can
    # corrupt the data-check-string for values containing '+' etc.
    pairs = []
    received_hash = None
    for chunk in init_data.split('&'):
        if '=' not in chunk:
            continue
        key, _, value = chunk.partition('=')
        if key == 'hash':
            received_hash = value
        else:
            pairs.append((key, value))
 
    if not received_hash:
        raise ValueError('Missing hash in initData')
 
    # Per Telegram's spec: build data-check-string from URL-decoded values,
    # sorted alphabetically by key, joined by newline.
    decoded_pairs = sorted((k, unquote(v)) for k, v in pairs)
    data_check_string = '\n'.join(f'{k}={v}' for k, v in decoded_pairs)
 
    # Two-stage HMAC: derive secret key from bot token, then sign data.
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
 
    # Freshness check — prevents replay of captured initData.
    parsed = dict(decoded_pairs)
    auth_date_str = parsed.get('auth_date')
    if not auth_date_str:
        raise ValueError('Missing auth_date in initData')
    try:
        auth_date = int(auth_date_str)
    except (TypeError, ValueError):
        raise ValueError('Malformed auth_date in initData')
 
    now = int(time.time())
    age = now - auth_date
    if age < -30:
        # Small clock skew tolerance; anything significantly in the future
        # indicates a forged or clock-manipulated payload.
        raise ValueError('auth_date is in the future')
    if age > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        raise ValueError('initData has expired; please reopen the app')
 
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
