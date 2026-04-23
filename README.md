# Spin Rewards — Backend API

The backend service for Spin Rewards, a Telegram-based real-money gaming platform. Built with Django and Django REST Framework.

---

## What This Service Does

This is the **single source of truth** for all financial and game operations on the platform. It handles:

- Telegram-based user authentication (JWT)
- Ledger-based wallet management (coin balance + cash balance)
- Server-side spin engine with configurable RTP and probability tiers
- Payment processing via Paystack and Flutterwave (deposit + withdrawal)
- KYC submission and admin review workflow
- Referral system with first-deposit bonuses
- Daily login reward streaks
- Admin APIs for RTP configuration, user management, KYC review, and analytics
- Async Telegram notifications via Celery

---

## Stack

| Component | Technology |
|---|---|
| Language | Python 3.11 |
| Framework | Django 4.2 + Django REST Framework |
| Database | PostgreSQL 15 |
| Cache / Queue | Redis 7 |
| Async Tasks | Celery |
| Auth | JWT (djangorestframework-simplejwt) |
| Containers | Docker + Docker Compose |
| Production Server | Gunicorn |

---

## Quick Start

### Requirements

- [Docker](https://www.docker.com/get-started) and Docker Compose (v2+)

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd backend
```

### 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and set at minimum:

| Variable | How to get it |
|---|---|
| `DJANGO_SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) on Telegram |
| `ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `PAYSTACK_SECRET_KEY` | From [Paystack Dashboard](https://dashboard.paystack.com) → Settings → API Keys |

### 3. Start the project

```bash
docker-compose up --build
```

This starts PostgreSQL, Redis, the Django API server (with auto-migration), and a Celery worker.

**The API is available at:** `http://localhost:8000`

### 4. Verify it's running

```bash
curl -X POST http://localhost:8000/api/v1/auth/telegram/ \
  -H "Content-Type: application/json" \
  -d '{"init_data": "test"}'
# Returns: {"error": true, "code": "INVALID_TELEGRAM_AUTH", ...}
# This confirms the server is up and auth validation is working.
```

### 5. (Optional) Create a Django superuser

```bash
docker-compose exec api python manage.py createsuperuser
```

Visit the Django admin at `http://localhost:8000/django-admin/`

---

## API Overview

Base URL: `http://localhost:8000/api/v1/`

All responses follow a consistent envelope:

```json
// Success
{ "success": true, "data": { ... } }

// Error
{ "error": true, "code": "ERROR_CODE", "message": "..." }
```

Authentication: `Authorization: Bearer <access_token>`

### Core Endpoints

| Endpoint | Description |
|---|---|
| `POST /auth/telegram/` | Authenticate via Telegram initData → returns JWT |
| `POST /auth/refresh/` | Refresh access token |
| `GET /wallet/` | Get coin + cash balances |
| `POST /spin/` | Execute a spin (stake + idempotency_key) |
| `GET /spin/tiers/` | Available stake tiers |
| `POST /deposits/` | Initiate a deposit → returns payment URL |
| `POST /webhooks/paystack/` | Paystack payment webhook |
| `POST /withdrawals/` | Request a withdrawal (KYC required) |
| `POST /kyc/` | Submit KYC information |
| `GET /referral/` | Get referral link and stats |
| `GET /rewards/daily/` | Daily reward status and streak |
| `POST /rewards/daily/claim/` | Claim daily reward |

Full API documentation: [`docs/api-reference.md`](docs/api-reference.md)

Full API contract with request/response schemas: [`../docs/BACKEND_API_CONTRACT.md`](../docs/BACKEND_API_CONTRACT.md)

---

## Project Structure

```
backend/
├── config/
│   ├── settings/
│   │   ├── base.py           # Shared settings
│   │   ├── development.py    # Dev environment
│   │   └── production.py     # Production environment
│   ├── urls.py               # Root URL configuration
│   ├── celery.py             # Celery app
│   └── wsgi.py
│
├── common/
│   ├── exceptions.py         # Custom exceptions + DRF error handler
│   ├── pagination.py         # Consistent pagination format
│   ├── permissions.py        # IsAdminUser permission
│   └── utils.py              # Telegram validation, encryption, payment signatures
│
├── apps/
│   ├── users/                # User model, Telegram auth, JWT
│   ├── wallet/               # Ledger-based wallet, WalletService
│   ├── spin/                 # RTP engine, SpinService
│   ├── payments/             # Deposits, Paystack, Flutterwave, webhooks
│   ├── withdrawals/          # Withdrawals, KYC gate, async payouts
│   ├── kyc/                  # KYC submission, NIN encryption, bank verification
│   ├── referrals/            # Referral links, first-deposit bonuses
│   ├── rewards/              # Daily rewards, streak system
│   ├── admin_panel/          # Admin APIs, RTP config, audit logs, analytics
│   └── notifications/        # Celery tasks → Telegram Bot API messages
│
├── docs/                     # Developer documentation
├── Dockerfile
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env.example
├── requirements.txt
└── Procfile                  # For Railway / Render
```

---

## Architecture Principles

### 1. Backend is the single source of truth

All financial and game logic happens here. The frontend and bot are display/navigation layers only. No exceptions.

### 2. Ledger-based wallet

There is no `balance` column. Every balance is computed from the transaction ledger (`SUM(amount) WHERE status='completed'`). This guarantees consistency.

### 3. Atomic financial operations

Every operation that touches money uses `db_transaction.atomic()`. Stake deduction and win credit happen in the same database transaction. If anything fails, everything rolls back.

### 4. Idempotency everywhere

- Spins: client provides `idempotency_key` — same key always returns same result
- Webhooks: `select_for_update()` on `DepositSession` prevents double-crediting
- Withdrawals: unique `reference` per request

### 5. Thin views, fat services

Views parse requests and return responses. All logic lives in `services.py` files.

---

## Key Engineering Rules

These must never be violated:

- No outcome generation on the frontend — spin results come from this backend only
- No direct balance updates — all changes go through `WalletService.credit()` / `.debit()`
- No wallet credit without webhook signature verification
- No withdrawal without approved KYC
- No partial updates — use `db_transaction.atomic()` everywhere money moves
- NIN must be encrypted at rest before storage (`common/utils.py encrypt()`)
- Audit logs are immutable — `AdminAuditLog` records cannot be updated or deleted

---

## Common Commands

```bash
# Start all services
docker-compose up

# Start in background
docker-compose up -d

# View API logs
docker-compose logs -f api

# View Celery worker logs
docker-compose logs -f worker

# Run migrations
docker-compose exec api python manage.py migrate

# Make new migrations after model changes
docker-compose exec api python manage.py makemigrations

# Open Django shell
docker-compose exec api python manage.py shell

# Run tests
docker-compose exec api python manage.py test

# Stop everything
docker-compose down

# Stop and wipe all data (including database)
docker-compose down -v
```

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/getting-started.md`](docs/getting-started.md) | Full setup instructions (Docker and manual) |
| [`docs/environment-variables.md`](docs/environment-variables.md) | Every env variable explained |
| [`docs/architecture.md`](docs/architecture.md) | System design, wallet model, spin engine, auth flow |
| [`docs/api-reference.md`](docs/api-reference.md) | Quick API endpoint reference |
| [`docs/deployment.md`](docs/deployment.md) | Railway, Render, and VPS deployment guides |
| [`../docs/BACKEND_SPEC.md`](../docs/BACKEND_SPEC.md) | Full backend engineering specification |
| [`../docs/BACKEND_API_CONTRACT.md`](../docs/BACKEND_API_CONTRACT.md) | Complete API contract with all request/response schemas |

---

## Environment Variables

See [`docs/environment-variables.md`](docs/environment-variables.md) for the full reference.

Critical variables at a glance:

```env
DJANGO_SECRET_KEY=         # Required. Long random string.
DJANGO_DEBUG=True          # Set False in production.
TELEGRAM_BOT_TOKEN=        # From BotFather.
ENCRYPTION_KEY=            # Fernet key. Back this up — losing it is permanent.
PAYSTACK_SECRET_KEY=       # sk_test_... for dev, sk_live_... for prod.
DB_HOST=localhost          # Use 'db' when running in Docker.
REDIS_URL=redis://localhost:6379/0   # Use 'redis://redis:6379/0' in Docker.
```

---

## Contributing

1. Always work in a feature branch — do not push directly to `main`
2. Run `python manage.py migrate` after pulling changes that include new migrations
3. All new business logic goes in `services.py`, not views 
4. Financial operations must use `db_transaction.atomic()`
5. Any PII (NIN, bank account details) must be handled according to the encryption policy in `common/utils.py`
