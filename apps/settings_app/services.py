"""
Service for reading SystemSetting values with cache + env fallback.

Resolution order for `get_setting('KEY')`:
  1. In-memory cache (per-process, TTL 60s)
  2. SystemSetting row in the database
  3. Environment variable (django settings)
  4. Hard-coded default in SettingKey.DEFAULTS
"""
import logging
import threading
import time
from decimal import Decimal
from typing import Optional

from django.conf import settings as dj_settings

from .models import SettingKey, SystemSetting

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60

# Simple per-process cache: { key: (value, expires_at) }
_cache: dict = {}
_cache_lock = threading.Lock()


def _read_from_db(key: str) -> Optional[Decimal]:
    try:
        row = SystemSetting.objects.get(key=key)
        return row.value
    except SystemSetting.DoesNotExist:
        return None


def _read_from_env(key: str) -> Optional[Decimal]:
    """Read the setting from Django settings / env (e.g. settings.COINS_PER_NGN)."""
    raw = getattr(dj_settings, key, None)
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except Exception:
        logger.warning('Invalid env value for %s: %r', key, raw)
        return None


def _hardcoded_default(key: str) -> Decimal:
    default_str, _ = SettingKey.DEFAULTS.get(key, ('0', ''))
    return Decimal(default_str)


def get_setting(key: str) -> Decimal:
    """Read a setting. Cached for 60s."""
    now = time.time()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

    value = _read_from_db(key)
    if value is None:
        value = _read_from_env(key)
    if value is None:
        value = _hardcoded_default(key)

    with _cache_lock:
        _cache[key] = (value, now + _CACHE_TTL_SECONDS)
    return value


def set_setting(key: str, value, updated_by=None) -> SystemSetting:
    """
    Upsert a setting. Invalidates the cache.

    Returns the SystemSetting row.
    """
    if key not in SettingKey.DEFAULTS:
        raise ValueError(f'Unknown setting key: {key}')

    if not isinstance(value, Decimal):
        value = Decimal(str(value))

    description = SettingKey.DEFAULTS[key][1]

    row, _ = SystemSetting.objects.update_or_create(
        key=key,
        defaults={
            'value': value,
            'description': description,
            'updated_by': updated_by,
        },
    )

    # Invalidate cache for this key
    with _cache_lock:
        _cache.pop(key, None)

    logger.info(
        'Setting updated: %s = %s by %s',
        key, value, getattr(updated_by, 'id', None),
    )
    return row


def invalidate_cache(key: Optional[str] = None):
    """Clear cache. Pass a key to clear just one, or None to clear all."""
    with _cache_lock:
        if key:
            _cache.pop(key, None)
        else:
            _cache.clear()


def get_all_settings() -> dict:
    """
    Return a dict of {key: {value, default, description, has_override}} for all known keys.
    Used by the admin settings endpoint.
    """
    db_rows = {row.key: row for row in SystemSetting.objects.all()}

    result = {}
    for key, (default, description) in SettingKey.DEFAULTS.items():
        db_row = db_rows.get(key)
        env_value = _read_from_env(key)
        result[key] = {
            'value': str(get_setting(key)),
            'default': default,
            'env_value': str(env_value) if env_value is not None else None,
            'description': description,
            'has_db_override': db_row is not None,
            'updated_at': db_row.updated_at.isoformat() if db_row else None,
        }
    return result
