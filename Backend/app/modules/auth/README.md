# Module 1 — Authentication hardening

This module owns every endpoint under `/api/auth/*`. It is intentionally
split from the rest of the codebase so security-sensitive logic can be
audited independently.

## Architecture

```
            ┌──────────────────────┐
            │   router.py          │ ← thin HTTP layer (Request, Redis client)
            └──────────┬───────────┘
                       │
            ┌──────────▼───────────┐
            │   service.py         │ ← all business logic; never raises HTTPException
            │  (AuthService)       │     directly, only `AppError` subclasses.
            └────┬─────────────┬───┘
                 │             │
        ┌────────▼──┐  ┌───────▼─────────────┐
        │ models.py │  │ mfa.py              │ ← pyotp wrappers
        └───────────┘  └─────────────────────┘
                 │
        ┌────────▼────────┐
        │ schemas.py      │ ← Pydantic in/out shapes
        └─────────────────┘
```

Cross-cutting helpers live outside the module so they can be reused:
* `app/core/security.py` — Argon2 hashing, JWT mint/decode, AES-GCM secret encryption, JTI revocation.
* `app/core/rate_limit.py` — Redis fixed-window counters.

## Endpoints (14 total)

| Method | Path                            | Purpose                                                 |
|--------|---------------------------------|---------------------------------------------------------|
| POST   | `/api/auth/login`               | Email + password. Returns access+refresh OR MFA challenge. |
| GET    | `/api/auth/me`                  | Current user profile (includes `mfaEnabled` flag).      |
| GET    | `/api/auth/users`               | Admin annuaire (national/ministry only — unchanged).    |
| POST   | `/api/auth/mfa/verify`          | Exchanges MFA challenge + TOTP for access+refresh.       |
| POST   | `/api/auth/refresh`             | Rotates the refresh token. Revokes the previous one.    |
| POST   | `/api/auth/logout`              | Revokes access (Redis JTI blacklist) + refresh (DB row).|
| POST   | `/api/auth/change-password`     | Self-service password change. Enforces history of 5.    |
| POST   | `/api/auth/forgot-password`     | Always 202 — never leaks email existence.               |
| POST   | `/api/auth/reset-password`      | Single-use reset token (30 min TTL).                    |
| POST   | `/api/auth/mfa/setup`           | Issues TOTP secret + QR URI + 10 recovery codes.        |
| POST   | `/api/auth/mfa/verify-setup`    | Activates the credential after the user scans the QR.   |
| POST   | `/api/auth/mfa/disable`         | Requires password + a valid TOTP or recovery code.      |
| GET    | `/api/auth/sessions`            | Lists the user's active refresh sessions.               |
| DELETE | `/api/auth/sessions/{id}`       | Manually revokes one session.                           |

## MFA flow

1. **Setup** — `POST /api/auth/mfa/setup` returns `{secret, qrCodeUri, recoveryCodes[10]}`.
   The plain `secret` is shown once so the user can scan it in Google Authenticator.
   The codes are also shown once and **never persisted in clear** (only Argon2 hashes).
2. **Activation** — `POST /api/auth/mfa/verify-setup` with a fresh TOTP. The
   credential row flips `enabled=true` and `User.mfaEnabled=true`.
3. **Login** — once `mfaEnabled`, `/login` no longer returns tokens directly;
   it returns `{mfaChallenge: <5-min JWT>}`. The client then POSTs the
   challenge + a TOTP to `/mfa/verify` to get real tokens.
4. **Recovery** — a recovery code can be substituted for the TOTP. Each code
   is single-use; consumed codes are removed from the JSONB array.
5. **Disable** — requires the current password **and** a valid code (double-check).

## Refresh rotation

* Every `/login` and `/mfa/verify` creates a row in `RefreshTokenSession`
  with `tokenHash = sha256(refreshToken)`. The DB is the source of truth.
* `/refresh` validates the JWT signature/expiry, then:
  1. Looks up the DB row by `tokenHash` and rejects revoked/expired.
  2. Marks the row `revokedAt=now, revokedReason="rotated"`.
  3. Adds the JTI to the Redis blacklist (TTL = remaining token life).
  4. Mints a new pair, persists the new session row.

## JTI blacklist (Redis)

* Every JWT carries a `jti` claim (UUID hex).
* `revoke_token(jti, exp)` writes `auth:revoked:<jti> = "1"` with TTL clamped
  to the remaining token life — entries garbage-collect themselves.
* `get_current_user` (in `app/shared/deps.py`) calls `is_token_revoked(jti)`
  for every authenticated request — fast (1 Redis ROUNDTRIP, ~1 ms LAN).
* If Redis is down, the blacklist check **fails open** (we keep serving
  traffic but log a warning). Refresh rotation still enforces revocation
  via the DB row.

## Rate limiting

| Key                                  | Limit         | Window |
|--------------------------------------|---------------|--------|
| `rl:login:email:<normalized-email>`  | 5 attempts    | 15 min |
| `rl:login:ip:<ip>`                   | 20 attempts   | 15 min |
| `rl:mfa:user:<user-id>`              | 10 attempts   | 15 min |
| `rl:pwreset:email:<email>`           | 3 attempts    | 60 min |
| `rl:pwreset:ip:<ip>`                 | 10 attempts   | 60 min |

Hit before Argon2 (so brute force cannot saturate CPU). On successful login
the per-email/per-IP counters are cleared so users aren't punished for past
typos.

## Audit log

`AuthAuditLog` is append-only. Every endpoint writes at least one row with
`{userId?, email, event, ipAddress, userAgent, success, failureReason?}`.
Events used: `LOGIN_SUCCESS|LOGIN_FAILED|MFA_SUCCESS|MFA_FAILED|LOGOUT|`
`REFRESH|PASSWORD_CHANGED|MFA_ENABLED|MFA_DISABLED|`
`PASSWORD_RESET_REQUESTED|PASSWORD_RESET_USED|RATE_LIMITED|SESSION_REVOKED`.

Index `(userId, createdAt DESC)` and `(email, createdAt DESC)` make the
admin UI's "show me recent events for <user/email>" queries instant.

## Backward compatibility

The `LoginResponse` schema is **byte-compatible** with the NestJS version:

* `accessToken: str | None` — populated for non-MFA users (unchanged behaviour
  for the Angular frontend).
* `refreshToken`, `mfaChallenge` — new optional fields; ignored by the
  existing client.
* `user` — same shape as before.

`MeResponse.user` gains two booleans (`mfaRequired`, `mfaEnabled`). Pydantic
serialisation always emits them — extra fields are ignored by the legacy
TypeScript model, so no breakage.

## Operations

* **MFA secret encryption** — `JWT_SECRET` doubles as the HKDF salt input
  for the AES-256-GCM key (`info="gestionee.mfa.v1"`). Rotating `JWT_SECRET`
  invalidates all stored TOTP secrets — operators must re-enroll users.
  Plan: add a secondary `MFA_KEY` env in a future migration if we need
  rotation independence.
* **Refresh-token cleanup** — `RefreshTokenSession` rows are kept forever
  for audit. A Celery beat job should periodically purge `expiresAt < now()
  - 90 days` — out of scope for Module 1 but trivial to add.
* **Reset token cleanup** — same idea: purge `PasswordResetToken` rows
  older than 7 days.

## Tests

`tests/integration/test_auth_module1.py` covers the full surface:
login (4) · MFA challenge / TOTP / recovery / expiry (5) ·
rate-limit (2) · refresh / rotation / expired / revoked (3) ·
logout (1) · change-password (3) · forgot/reset (4) ·
MFA setup/verify/disable (3) · sessions list/revoke (2) ·
audit-log (3) · /me byte-compat (1) · crypto primitives (3) — ≈34 tests.
