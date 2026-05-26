"""
Backend auth smoke-test.

Generates a genuinely-valid Telegram initData string using your real bot
token, then POSTs it to your local Django backend. No Telegram client, no
tunnel, no frontend needed.

Usage:
    1. Make sure your Django server is running:
         python manage.py runserver 8000

    2. Set your bot token (the same one in Django settings):
         export TELEGRAM_BOT_TOKEN="123456:ABC..."

    3. Run:
         python test_auth_backend.py

What you should see on success:
    ✓ HTTP 200
    ✓ JWT access_token and refresh_token returned
    ✓ User object echoed back

What you should see on failure:
    ✗ HTTP 401 with "Telegram authentication failed"
       → bot token mismatch, OR your validate_telegram_init_data has a bug

    ✗ HTTP 500
       → unhandled exception in backend, check Django logs

    ✗ Connection refused
       → Django server isn't running on localhost:8000
"""
import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import quote

import requests  # pip install requests


# ─── Config ──────────────────────────────────────────────────────────────────

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
BACKEND_URL = os.environ.get('BACKEND_URL', 'http://localhost:8000')
AUTH_ENDPOINT = f'{BACKEND_URL}/api/v1/auth/telegram/'
ME_ENDPOINT = f'{BACKEND_URL}/api/v1/users/me/'

# Fake Telegram user for testing. Pick a high number so you don't clash
# with a real user ID if you ever point this at staging.
TEST_USER = {
    'id': 999000001,
    'first_name': 'Richard',
    'last_name': 'Uzor',
    'username': 'richarduzor_test',
    'language_code': 'en',
}


# ─── initData builder ────────────────────────────────────────────────────────

def build_init_data(bot_token: str, user: dict, auth_date: int | None = None) -> str:
    """Produces a valid signed initData string — what Telegram sends."""
    auth_date = auth_date or int(time.time())
    fields = {
        'user': json.dumps(user, separators=(',', ':')),
        'auth_date': str(auth_date),
        'query_id': 'AAHtest_backend_smoke_test',
    }

    # Data-check-string: sorted, joined by \n
    data_check_string = '\n'.join(
        f'{k}={v}' for k, v in sorted(fields.items())
    )

    # Two-stage HMAC per Telegram spec
    secret = hmac.new(
        b'WebAppData', bot_token.encode(), hashlib.sha256
    ).digest()
    signature = hmac.new(
        secret, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    # Build wire-format query string (values URL-encoded)
    parts = [f'{k}={quote(v, safe="")}' for k, v in fields.items()]
    parts.append(f'hash={signature}')
    return '&'.join(parts)


# ─── Test runner ─────────────────────────────────────────────────────────────

def pretty(prefix: str, resp):
    print(f'\n{prefix}: HTTP {resp.status_code}')
    try:
        print(json.dumps(resp.json(), indent=2))
    except Exception:
        print(resp.text)


def main():
    if not BOT_TOKEN:
        print('ERROR: set TELEGRAM_BOT_TOKEN env var.')
        sys.exit(1)

    print(f'→ Backend: {BACKEND_URL}')
    print(f'→ Bot token: {BOT_TOKEN[:10]}... (len={len(BOT_TOKEN)})')

    # ── Test 1: Valid initData should authenticate ────────────────────────
    print('\n' + '=' * 60)
    print('TEST 1: Valid initData → expect HTTP 200 + tokens')
    print('=' * 60)
    init_data = build_init_data(BOT_TOKEN, TEST_USER)
    resp = requests.post(AUTH_ENDPOINT, json={'init_data': init_data})
    pretty('Response', resp)
    if resp.status_code != 200:
        print('\n❌ FAILED — investigate before continuing.')
        sys.exit(1)

    tokens = resp.json()['data']
    access_token = tokens['access_token']
    refresh_token = tokens['refresh_token']
    print(f'\n✓ Got access token ({len(access_token)} chars)')
    print(f'✓ Got refresh token ({len(refresh_token)} chars)')

    # ── Test 2: Access token should unlock /users/me/ ─────────────────────
    print('\n' + '=' * 60)
    print('TEST 2: Authenticated /users/me/ → expect HTTP 200')
    print('=' * 60)
    me = requests.get(ME_ENDPOINT, headers={'Authorization': f'Bearer {access_token}'})
    pretty('Response', me)
    if me.status_code != 200:
        print('\n❌ FAILED — JWT auth not working end-to-end.')
        sys.exit(1)

    # ── Test 3: No auth header → 401 ──────────────────────────────────────
    print('\n' + '=' * 60)
    print('TEST 3: /users/me/ with no auth → expect HTTP 401')
    print('=' * 60)
    unauth = requests.get(ME_ENDPOINT)
    pretty('Response', unauth)
    assert unauth.status_code == 401, f'Expected 401, got {unauth.status_code}'

    # ── Test 4: Tampered initData (bad hash) → 401 ────────────────────────
    print('\n' + '=' * 60)
    print('TEST 4: Tampered hash → expect HTTP 401')
    print('=' * 60)
    tampered = build_init_data(BOT_TOKEN, TEST_USER)
    tampered = tampered.rsplit('hash=', 1)[0] + 'hash=' + '0' * 64
    resp = requests.post(AUTH_ENDPOINT, json={'init_data': tampered})
    pretty('Response', resp)
    assert resp.status_code == 401, f'Expected 401, got {resp.status_code}'

    # ── Test 5: Expired initData (auth_date > 5 min ago) → 401 ────────────
    print('\n' + '=' * 60)
    print('TEST 5: Expired auth_date → expect HTTP 401')
    print('=' * 60)
    expired = build_init_data(BOT_TOKEN, TEST_USER, auth_date=int(time.time()) - 3600)
    resp = requests.post(AUTH_ENDPOINT, json={'init_data': expired})
    pretty('Response', resp)
    assert resp.status_code == 401, f'Expected 401, got {resp.status_code}'

    # ── Test 6: Error shape does NOT leak internals ───────────────────────
    print('\n' + '=' * 60)
    print('TEST 6: Error message should not leak "signature"/"hmac"')
    print('=' * 60)
    msg = resp.json().get('message', '').lower()
    assert 'signature' not in msg and 'hmac' not in msg, \
        f'Error message leaks internals: {msg}'
    print(f'✓ Generic error message: "{resp.json().get("message")}"')

    print('\n' + '=' * 60)
    print('ALL TESTS PASSED ✓')
    print('=' * 60)


if __name__ == '__main__':
    main()