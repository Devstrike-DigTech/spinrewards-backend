# Architecture Overview

## System Role

This backend is the **single source of truth** for all financial and game operations in the Spin Rewards platform. It exposes a REST API consumed by:

- The Telegram Mini App (React)
- The Telegram Bot (Telegraf)
- The Admin Dashboard (React)

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Framework | Django 4.2 + Django REST Framework |
| Database | PostgreSQL 15 |
| Cache & Queue Broker | Redis 7 |
| Async Task Queue | Celery |
| Auth | JWT via `djangorestframework-simplejwt` |
| Containerisation | Docker + Docker Compose |
| Production Server | Gunicorn + WhiteNoise |

---

## Project Layout

```
backend/
├── config/
│   ├── settings/
│   │   ├── base.py          # Shared settings for all environments
│   │   ├── development.py   # Dev overrides (debug, permissive CORS)
│   │   └── production.py    # Prod overrides (HTTPS, strict CORS, HSTS)
│   ├── urls.py              # Root URL router — all /api/v1/ routes
│   ├── celery.py            # Celery app init and autodiscovery
│   └── wsgi.py / asgi.py
│
├── common/
│   ├── exceptions.py        # Custom exception classes + DRF handler
│   ├── pagination.py        # Standard paginated response format
│   ├── permissions.py       # IsAdminUser permission class
│   └── utils.py             # Telegram auth validation, encryption, signatures
│
├── apps/
│   ├── users/               # User model, Telegram auth, JWT issuance
│   ├── wallet/              # Wallet model, Transaction ledger, WalletService
│   ├── spin/                # RTPTier, RTPOutcome, SpinResult, SpinService
│   ├── payments/            # DepositSession, payment provider integrations, webhooks
│   ├── withdrawals/         # WithdrawalRequest, WithdrawalService, payout task
│   ├── kyc/                 # KYC model, NIN encryption, bank verification
│   ├── referrals/           # Referral model, first-deposit bonus
│   ├── rewards/             # DailyReward, streak logic
│   ├── admin_panel/         # AdminAuditLog, all admin-facing views and serializers
│   └── notifications/       # Celery tasks for Telegram Bot API messages
│
├── docs/                    # This documentation
├── Dockerfile
├── docker-compose.yml
├── docker-compose.prod.yml
├── manage.py
└── requirements.txt
```

---

## Architecture Pattern

### Thin Views, Fat Services

All business logic lives in `services.py` files, not in views.

**Views** are responsible for:
1. Deserializing and validating the request
2. Calling the appropriate service method
3. Returning the formatted response

**Services** are responsible for:
1. All business logic
2. Database operations
3. Calling other services
4. Triggering Celery tasks

```
Request → View → Serializer (validate) → Service → Response
```

This makes logic testable in isolation and keeps views clean.

---

## Authentication Flow

```
1. Telegram Mini App sends Telegram.WebApp.initData to POST /api/v1/auth/telegram/
2. Backend validates HMAC-SHA256 signature using BOT_TOKEN (WebAppData key)
3. User is created if new, or retrieved if existing
4. Wallet is auto-created for new users
5. JWT access token (24h) + refresh token (30d) returned
6. All subsequent API requests include: Authorization: Bearer <access_token>
7. On 401: frontend calls POST /api/v1/auth/refresh/ to rotate tokens
```

---

## Wallet System

The wallet is **ledger-based**. There is no `balance` column anywhere.

Every balance is computed as `SUM(amount)` on the `transactions` table, filtered by `balance_type` and `status=completed`.

```
coin_balance = SUM(transactions WHERE balance_type='coin' AND status='completed')
```

**Why:** This guarantees the balance is always consistent with what actually happened. There is no way for balances to drift out of sync.

**Two balance types:**
- `coin` — play currency. Credited on deposits, daily rewards, referral bonuses. Debited on spins.
- `cash` — withdrawable winnings. Credited when user wins a spin. Debited on withdrawal.

---

## Spin Engine

```
POST /api/v1/spin/
  ↓
1. Check idempotency_key — if already processed, return original result
2. Find matching RTPTier for stake amount
3. Load active RTPOutcomes, validate probabilities sum to 100%
4. Debit stake from coin balance (atomic)
5. Generate cryptographically secure random number via secrets.randbelow()
6. Map RNG value to outcome using cumulative probability ranges
7. Calculate win_amount = stake × multiplier
8. Credit win_amount to coin balance if > 0 (same atomic transaction)
9. Create SpinResult record
10. Return result
```

**Critical properties:**
- Entirely server-side — client never influences outcome
- Atomic — stake and win are one DB transaction; it either fully succeeds or fully rolls back
- Idempotent — duplicate requests with the same key return the same result, not a new spin
- Auditable — RNG value is stored for every spin

---

## Payment Flow

```
Deposit:
  Client → POST /deposits/ → Backend calls Paystack API → Returns payment_url
  User pays on Paystack → Paystack sends webhook → POST /webhooks/paystack/
  Backend verifies signature → Credits wallet → Sends Telegram notification

Withdrawal:
  Client → POST /withdrawals/ → Backend validates KYC + balance
  Debits cash balance → Creates WithdrawalRequest → Triggers Celery task
  Celery calls payout provider → On success: complete / On failure: reverse funds
```

**Idempotency on webhooks:** The system uses `select_for_update()` on `DepositSession` before processing. If the session is already `completed`, the webhook is silently ignored. This prevents double-crediting on duplicate webhook delivery.

---

## Celery Tasks

| Task | Trigger | Purpose |
|---|---|---|
| `process_withdrawal_payout` | After withdrawal request | Submit payout to provider, retry on failure, reverse on max retries |
| `notify_deposit_success` | After webhook confirms deposit | Send Telegram message to user |
| `notify_withdrawal_complete` | After payout confirmed | Send Telegram message |
| `notify_withdrawal_failed` | After payout reversal | Send Telegram message |
| `notify_kyc_approved` | After admin approves KYC | Send Telegram message |
| `notify_kyc_rejected` | After admin rejects KYC | Send Telegram message |

---

## Admin Audit Log

Every admin action creates an `AdminAuditLog` record with:
- Who made the change (`admin`)
- What was changed (`action`, `target_model`, `target_id`)
- Full before and after state as JSON (`previous_state`, `new_state`)
- Timestamp and IP address

Audit logs are **immutable** — the model's `save()` and `delete()` methods raise `PermissionError` if called on existing records.

---

## Security Properties

| Property | Implementation |
|---|---|
| Auth | JWT with 24h access tokens, 30d refresh tokens with rotation + blacklisting |
| Telegram validation | HMAC-SHA256 on initData using `WebAppData` secret key |
| Webhook validation | HMAC-SHA512 for Paystack, secret hash for Flutterwave |
| NIN encryption | AES-256 via Fernet symmetric encryption, key stored in env |
| No negative balances | Enforced in `WalletService.debit()` before any DB write |
| Atomic transactions | `ATOMIC_REQUESTS=True` + explicit `db_transaction.atomic()` blocks |
| Race condition prevention | `select_for_update()` on wallet rows during credit/debit |
