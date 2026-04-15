# API Reference

> Full specification is in the project-level docs at `../../docs/BACKEND_API_CONTRACT.md`.
> This file is a quick lookup for the most commonly used endpoints during development.

---

## Base URL

```
Development:  http://localhost:8000/api/v1
Production:   https://api.spinrewards.com/api/v1
```

## Authentication

All endpoints except auth and webhooks require:
```
Authorization: Bearer <access_token>
```

---

## Quick Reference

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/auth/telegram/` | None | Authenticate via Telegram initData |
| POST | `/auth/refresh/` | None | Refresh access token |
| GET | `/users/me/` | JWT | Get own profile |
| GET | `/wallet/` | JWT | Get wallet balances |
| GET | `/wallet/transactions/` | JWT | Transaction history |
| POST | `/spin/` | JWT | Execute a spin |
| GET | `/spin/tiers/` | JWT | Available stake tiers |
| GET | `/spin/history/` | JWT | Spin history |
| POST | `/deposits/` | JWT | Initiate deposit |
| GET | `/deposits/list/` | JWT | Deposit history |
| POST | `/webhooks/paystack/` | Sig | Paystack webhook |
| POST | `/webhooks/flutterwave/` | Sig | Flutterwave webhook |
| POST | `/withdrawals/` | JWT | Request withdrawal |
| GET | `/withdrawals/list/` | JWT | Withdrawal history |
| GET | `/withdrawals/{id}/` | JWT | Withdrawal detail |
| POST | `/kyc/` | JWT | Submit KYC |
| GET | `/kyc/status/` | JWT | KYC status |
| POST | `/kyc/document/` | JWT | Upload KYC document |
| GET | `/referral/` | JWT | Referral info |
| GET | `/referral/list/` | JWT | Referred users list |
| GET | `/rewards/daily/` | JWT | Daily reward status |
| POST | `/rewards/daily/claim/` | JWT | Claim daily reward |
| GET | `/admin/rtp/tiers/` | Admin | List RTP tiers |
| POST | `/admin/rtp/tiers/` | Admin | Create RTP tier |
| PATCH | `/admin/rtp/tiers/{id}/` | Admin | Update tier |
| DELETE | `/admin/rtp/tiers/{id}/` | Admin | Deactivate tier |
| GET | `/admin/users/` | Admin | List users |
| GET | `/admin/users/{id}/` | Admin | User detail |
| PATCH | `/admin/users/{id}/` | Admin | Update user |
| GET | `/admin/kyc/` | Admin | List KYC submissions |
| POST | `/admin/kyc/{id}/approve/` | Admin | Approve KYC |
| POST | `/admin/kyc/{id}/reject/` | Admin | Reject KYC |
| GET | `/admin/withdrawals/` | Admin | List withdrawals |
| POST | `/admin/withdrawals/{id}/approve/` | Admin | Approve withdrawal |
| POST | `/admin/withdrawals/{id}/reject/` | Admin | Reject withdrawal |
| GET | `/admin/analytics/overview/` | Admin | Platform analytics |
| GET | `/admin/audit-logs/` | Admin | Audit log |

---

## Standard Response Format

**Success:**
```json
{ "success": true, "data": { ... } }
```

**Error:**
```json
{ "error": true, "code": "ERROR_CODE", "message": "Human-readable description" }
```

**Validation error (422):**
```json
{
  "error": true,
  "code": "VALIDATION_ERROR",
  "message": "Validation failed.",
  "fields": { "amount": ["This field is required."] }
}
```

---

## Error Codes

| Code | HTTP | Meaning |
|---|---|---|
| `INVALID_TELEGRAM_AUTH` | 401 | Bad initData signature |
| `TOKEN_EXPIRED` | 401 | JWT expired |
| `UNAUTHORIZED` | 403 | Not allowed |
| `KYC_REQUIRED` | 403 | KYC must be approved |
| `INSUFFICIENT_FUNDS` | 400 | Balance too low |
| `INVALID_STAKE` | 400 | No tier for this stake |
| `DUPLICATE_REQUEST` | 409 | Idempotency key already used |
| `INVALID_PROBABILITY` | 400 | Probabilities ≠ 100% |
| `BANK_VERIFICATION_FAILED` | 400 | Could not verify bank account |
| `REWARD_ALREADY_CLAIMED` | 400 | Daily reward already claimed |
| `PROVIDER_ERROR` | 502 | Payment provider unavailable |
| `CONFIGURATION_ERROR` | 500 | RTP misconfigured by admin |
| `VALIDATION_ERROR` | 422 | Input validation failed |
| `NOT_FOUND` | 404 | Resource not found |
| `RATE_LIMITED` | 429 | Too many requests |
| `SERVER_ERROR` | 500 | Unexpected error |

---

## Spin Endpoint — Critical Notes

```
POST /api/v1/spin/

Body:
{
  "stake": "1000.00",
  "idempotency_key": "spin_<userId>_<timestamp>_<random>"
}
```

- The `idempotency_key` must be **unique per spin attempt**. Recommended format: `spin_{userId}_{Date.now()}_{random4chars}`
- If the same key is sent twice, the second call returns the **original result** — no new spin is executed
- Do **not** start the wheel animation until the response is received
- The stake is deducted and the outcome is final before the response is returned

---

> For complete request/response schemas, see `../../docs/BACKEND_API_CONTRACT.md`
