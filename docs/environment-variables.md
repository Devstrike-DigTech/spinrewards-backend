# Environment Variables Reference

All environment variables are loaded from a `.env` file in the `backend/` root.
Copy `.env.example` to `.env` and fill in each value before running the project.

**Never commit `.env` to version control.** The `.gitignore` already excludes it.

---

## Django Core

| Variable | Required | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | Yes | Long random string used for cryptographic signing. Generate with: `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `DJANGO_DEBUG` | Yes | `True` for local dev, `False` for production |
| `DJANGO_ALLOWED_HOSTS` | Yes | Comma-separated. e.g., `localhost,127.0.0.1` (dev) or `api.spinrewards.com` (prod) |

---

## Database

| Variable | Required | Default | Description |
|---|---|---|---|
| `DB_NAME` | Yes | `spinrewards` | PostgreSQL database name |
| `DB_USER` | Yes | `postgres` | PostgreSQL username |
| `DB_PASSWORD` | Yes | — | PostgreSQL password |
| `DB_HOST` | Yes | `localhost` | Database host. Use `db` when running in Docker |
| `DB_PORT` | No | `5432` | PostgreSQL port |

---

## Redis

| Variable | Required | Default | Description |
|---|---|---|---|
| `REDIS_URL` | Yes | `redis://localhost:6379/0` | Full Redis connection URL. Use `redis://redis:6379/0` in Docker |

---

## Telegram

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | From BotFather. Format: `123456789:ABCdef...` |

---

## JWT

| Variable | Required | Description |
|---|---|---|
| `JWT_SIGNING_KEY` | No | Signing key for JWT tokens. Defaults to `DJANGO_SECRET_KEY` if left blank. Set explicitly in production for security isolation |

---

## Encryption

| Variable | Required | Description |
|---|---|---|
| `ENCRYPTION_KEY` | Yes | Fernet symmetric key used to encrypt NIN and other PII at rest. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

> **Important:** If you lose or rotate this key, previously encrypted data (NINs) cannot be decrypted. Back this up securely in production.

---

## Payments

| Variable | Required | Description |
|---|---|---|
| `PAYSTACK_SECRET_KEY` | Yes | Paystack secret key. Use `sk_test_...` for development, `sk_live_...` for production |
| `PAYSTACK_PUBLIC_KEY` | No | Paystack public key (not used server-side currently) |
| `FLUTTERWAVE_SECRET_KEY` | No | Flutterwave secret key. Required if using Flutterwave as a payment method |
| `FLUTTERWAVE_SECRET_HASH` | No | Flutterwave webhook verification hash. Set in Flutterwave dashboard |

---

## CORS

| Variable | Required | Description |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | Yes (prod) | Comma-separated list of allowed frontend origins. e.g., `https://app.spinrewards.com,https://admin.spinrewards.com` |

In development, `CORS_ALLOW_ALL_ORIGINS = True` is set automatically — this variable is only used in production settings.

---

## Frontend URLs

| Variable | Required | Description |
|---|---|---|
| `MINI_APP_URL` | Yes | Base URL of the Telegram Mini App. Used for payment callback URLs and CORS |
| `ADMIN_DASHBOARD_URL` | No | Base URL of the admin dashboard |

---

## Example `.env` for Local Development

```env
DJANGO_SECRET_KEY=dev-secret-key-change-in-production-please
DJANGO_DEBUG=True
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

DB_NAME=spinrewards
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432

REDIS_URL=redis://localhost:6379/0

TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ

ENCRYPTION_KEY=<output of Fernet.generate_key()>

PAYSTACK_SECRET_KEY=sk_test_xxxxxxxxxxxxxxxxxxxxxx
PAYSTACK_PUBLIC_KEY=pk_test_xxxxxxxxxxxxxxxxxxxxxx
FLUTTERWAVE_SECRET_KEY=FLWSECK_TEST-xxxxxx
FLUTTERWAVE_SECRET_HASH=your-flutterwave-hash

CORS_ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000

MINI_APP_URL=http://localhost:5173
ADMIN_DASHBOARD_URL=http://localhost:3000
```
