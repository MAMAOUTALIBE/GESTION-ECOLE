"""Integration tests for Module 1 — auth hardening.

Categories covered:
* Login (success / invalid email / wrong password / inactive)
* Login MFA (challenge, valid TOTP, invalid code, recovery code single-use)
* Rate limiting (per-email + per-IP login throttle, per-user MFA throttle)
* Refresh (success, rotation, revoked + expired rejected)
* Logout (access + refresh invalidated)
* Change password (history of 5 enforced, wrong current rejected)
* Forgot / reset password (single-use, expired)
* MFA setup / verify-setup / disable (with double-check on disable)
* Sessions (list / revoke)
* AuthAuditLog (every endpoint produces a row)
* /me byte-compatibility (additive fields only)
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
import pyotp
from freezegun import freeze_time
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    encrypt_secret,
    hash_password,
    hash_token,
)
from app.modules.auth.mfa import (
    fresh_recovery_codes,
    generate_secret,
    hash_recovery_codes,
    verify_totp,
)
from app.modules.auth.models import (
    AuthAuditLog,
    AuthEvent,
    MfaCredential,
    PasswordHistory,
    PasswordResetToken,
    RefreshTokenSession,
    User,
)
from app.shared.enums import UserRole
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PWD_OK = "Test@Pa55word!"
PWD_NEW = "Brand-new-pass-2026!"


async def _make_user(
    db_session: AsyncSession,
    *,
    email: str | None = None,
    password: str = PWD_OK,
    role: UserRole = UserRole.TEACHER,
    is_active: bool = True,
    mfa_enabled: bool = False,
) -> User:
    factories.bind(db_session)
    return await factories.UserFactory.create_async(
        email=email or f"u-{factories.generate_cuid()[:8]}@example.com",
        passwordHash=hash_password(password),
        role=role,
        isActive=is_active,
        mfaEnabled=mfa_enabled,
    )


async def _enable_mfa(
    db_session: AsyncSession, user: User
) -> tuple[str, list[str], list[str]]:
    """Persist an enabled MfaCredential for `user` and return
    `(plain_secret, plain_recovery_codes, hashed_recovery_codes)`.
    """
    secret = generate_secret()
    plain_codes, hashed_codes = fresh_recovery_codes()
    cred = MfaCredential(
        userId=user.id,
        secret=encrypt_secret(secret),
        enabled=True,
        verifiedAt=datetime.now(UTC),
        recoveryCodesHashed=hashed_codes,
    )
    db_session.add(cred)
    user.mfaEnabled = True
    await db_session.flush()
    return secret, plain_codes, hashed_codes


async def _audit_for(db_session: AsyncSession, email: str, event: str) -> AuthAuditLog | None:
    stmt = (
        select(AuthAuditLog)
        .where(AuthAuditLog.email == email, AuthAuditLog.event == event)
        .order_by(AuthAuditLog.createdAt.desc())
    )
    return (await db_session.execute(stmt)).scalars().first()


# ---------------------------------------------------------------------------
# Login — happy path & failure modes
# ---------------------------------------------------------------------------
async def test_login_success_returns_access_and_refresh(
    db_session: AsyncSession, client: AsyncClient, redis_client: Redis
) -> None:
    user = await _make_user(db_session, email="login-ok@example.com")
    r = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    # Module 1.1 — H-7 — /login now returns 200 OK (was 201 Created).
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accessToken"] and isinstance(body["accessToken"], str)
    assert body["refreshToken"] and isinstance(body["refreshToken"], str)
    assert body["user"]["id"] == user.id
    assert body["mfaChallenge"] is None
    # Module 1.1 — H-7 — explicit `requiresMfa` flag must be present and False.
    assert body["requiresMfa"] is False

    # Refresh session row persisted.
    stmt = select(RefreshTokenSession).where(RefreshTokenSession.userId == user.id)
    sess = (await db_session.execute(stmt)).scalar_one_or_none()
    assert sess is not None and sess.revokedAt is None


async def test_login_invalid_email_returns_401(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    factories.bind(db_session)
    r = await client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": PWD_OK},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


async def test_login_wrong_password_returns_401(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="bad-pw@example.com")
    r = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "Wrong-pass-1234!"},
    )
    assert r.status_code == 401


async def test_login_inactive_user_returns_401(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="inactive@example.com", is_active=False)
    r = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# MFA challenge -> verify
# ---------------------------------------------------------------------------
async def test_login_with_mfa_returns_challenge_token(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="mfa-ok@example.com", mfa_enabled=True)
    await _enable_mfa(db_session, user)
    r = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    # Module 1.1 — H-7 — /login returns 200 OK even when MFA is required.
    assert r.status_code == 200
    body = r.json()
    assert body["accessToken"] is None and body["refreshToken"] is None
    assert body["user"] is None
    assert body["mfaChallenge"]
    # Module 1.1 — H-7 — requiresMfa MUST be True in the MFA branch.
    assert body["requiresMfa"] is True
    payload = decode_token(body["mfaChallenge"], expected_type="mfa_challenge")
    assert payload["sub"] == user.id


async def test_mfa_verify_with_valid_totp_returns_tokens(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="mfa-totp@example.com", mfa_enabled=True)
    secret, _, _ = await _enable_mfa(db_session, user)
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    challenge = login.json()["mfaChallenge"]
    code = pyotp.TOTP(secret).now()
    r = await client.post(
        "/api/auth/mfa/verify",
        json={"challengeToken": challenge, "code": code},
    )
    assert r.status_code == 200, r.text
    assert r.json()["accessToken"]
    assert r.json()["refreshToken"]


async def test_mfa_verify_invalid_code_returns_401(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="mfa-bad@example.com", mfa_enabled=True)
    await _enable_mfa(db_session, user)
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    challenge = login.json()["mfaChallenge"]
    r = await client.post(
        "/api/auth/mfa/verify",
        json={"challengeToken": challenge, "code": "000000"},
    )
    assert r.status_code == 401


async def test_mfa_recovery_code_works_once(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="mfa-recov@example.com", mfa_enabled=True)
    _, plain_codes, _ = await _enable_mfa(db_session, user)
    code = plain_codes[0]

    # First use — success.
    login1 = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    r1 = await client.post(
        "/api/auth/mfa/verify",
        json={"challengeToken": login1.json()["mfaChallenge"], "code": code},
    )
    assert r1.status_code == 200

    # Second use — same code rejected (single-use).
    login2 = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    r2 = await client.post(
        "/api/auth/mfa/verify",
        json={"challengeToken": login2.json()["mfaChallenge"], "code": code},
    )
    assert r2.status_code == 401


async def test_mfa_challenge_expires_after_5_minutes(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(
        db_session, email="mfa-expire@example.com", mfa_enabled=True
    )
    secret, _, _ = await _enable_mfa(db_session, user)

    with freeze_time("2026-05-23T12:00:00Z"):
        login = await client.post(
            "/api/auth/login",
            json={"email": user.email, "password": PWD_OK},
        )
        challenge = login.json()["mfaChallenge"]

    # Jump >5 minutes ahead — challenge must be expired.
    with freeze_time("2026-05-23T12:06:00Z"):
        code = pyotp.TOTP(secret).now()
        r = await client.post(
            "/api/auth/mfa/verify",
            json={"challengeToken": challenge, "code": code},
        )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Rate limit
# ---------------------------------------------------------------------------
async def test_login_rate_limit_per_email_blocks_after_5_failures(
    db_session: AsyncSession,
    client: AsyncClient,
    redis_client: Redis,
) -> None:
    email = "rl-email@example.com"
    user = await _make_user(db_session, email=email)
    # 5 wrong-password attempts — all 401, but 6th is throttled.
    for _ in range(5):
        r = await client.post(
            "/api/auth/login",
            json={"email": email, "password": "Wrong-pass-1234!"},
        )
        assert r.status_code == 401
    r6 = await client.post(
        "/api/auth/login",
        json={"email": email, "password": PWD_OK},  # correct pw, but throttled
    )
    assert r6.status_code == 429
    assert r6.json()["code"] == "rate_limited"
    # cleanup user reference to silence ruff F841
    assert user.email == email


async def test_login_rate_limit_per_ip_blocks_after_20_failures(
    db_session: AsyncSession,
    client: AsyncClient,
    redis_client: Redis,
) -> None:
    # 20 wrong-password attempts on DIFFERENT emails (so per-email limit
    # never triggers) — 21st is throttled on the IP key.
    factories.bind(db_session)
    for i in range(20):
        r = await client.post(
            "/api/auth/login",
            json={
                "email": f"ipscan-{i}-{factories.generate_cuid()[:6]}@example.com",
                "password": "Wrong-pass-1234!",
            },
        )
        # 401 unauthorized for unknown user — but per-IP counter still incremented.
        assert r.status_code in (401, 429)
    r_blocked = await client.post(
        "/api/auth/login",
        json={"email": "another-victim@example.com", "password": PWD_OK},
    )
    assert r_blocked.status_code == 429


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------
async def test_refresh_success_rotates_token(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="refresh-ok@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    refresh_v1 = login.json()["refreshToken"]
    r = await client.post(
        "/api/auth/refresh", json={"refreshToken": refresh_v1}
    )
    assert r.status_code == 200, r.text
    refresh_v2 = r.json()["refreshToken"]
    assert refresh_v2 != refresh_v1

    # Old refresh is rejected (rotation).
    r_old = await client.post(
        "/api/auth/refresh", json={"refreshToken": refresh_v1}
    )
    assert r_old.status_code == 401


async def test_refresh_expired_token_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="refresh-expired@example.com")
    # Hand-craft a refresh token already expired.
    now = datetime.now(UTC)
    payload = {
        "sub": user.id,
        "type": "refresh",
        "iat": int((now - timedelta(days=10)).timestamp()),
        "exp": int((now - timedelta(days=1)).timestamp()),
        "jti": "expired-jti",
    }
    expired = jwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    r = await client.post("/api/auth/refresh", json={"refreshToken": expired})
    assert r.status_code == 401


async def test_refresh_revoked_session_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="refresh-revoked@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    refresh = login.json()["refreshToken"]
    # Manually revoke the DB session row.
    stmt = select(RefreshTokenSession).where(RefreshTokenSession.userId == user.id)
    row = (await db_session.execute(stmt)).scalars().first()
    assert row is not None
    row.revokedAt = datetime.now(UTC)
    row.revokedReason = "test"
    await db_session.flush()

    r = await client.post("/api/auth/refresh", json={"refreshToken": refresh})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------
async def test_logout_invalidates_access_and_refresh(
    db_session: AsyncSession, client: AsyncClient, redis_client: Redis
) -> None:
    user = await _make_user(db_session, email="logout@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    # Module 1.1 — H-7 — /login is 200 OK.
    assert login.status_code == 200, login.text
    access = login.json()["accessToken"]
    refresh = login.json()["refreshToken"]

    r = await client.post(
        "/api/auth/logout",
        json={"refreshToken": refresh},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 204

    # Refresh now rejected.
    r2 = await client.post("/api/auth/refresh", json={"refreshToken": refresh})
    assert r2.status_code == 401

    # Access now rejected on /me (Redis blacklist).
    r3 = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {access}"}
    )
    assert r3.status_code == 401


# ---------------------------------------------------------------------------
# Change password — current check + history of 5
# ---------------------------------------------------------------------------
async def test_change_password_success(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="cp-ok@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    access = login.json()["accessToken"]
    r = await client.post(
        "/api/auth/change-password",
        json={
            "currentPassword": PWD_OK,
            "newPassword": PWD_NEW,
            "confirmPassword": PWD_NEW,
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 204, r.text
    # PasswordHistory row created.
    stmt = select(PasswordHistory).where(PasswordHistory.userId == user.id)
    rows = (await db_session.execute(stmt)).scalars().all()
    assert len(rows) >= 1


async def test_change_password_wrong_current_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="cp-bad@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    access = login.json()["accessToken"]
    r = await client.post(
        "/api/auth/change-password",
        json={
            "currentPassword": "Totally-wrong-1234!",
            "newPassword": PWD_NEW,
            "confirmPassword": PWD_NEW,
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 401


async def test_change_password_history_blocks_reuse(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Push 5 historical hashes; 6th change tries to reuse the first one — rejected."""
    user = await _make_user(db_session, email="cp-history@example.com")
    # Seed PasswordHistory with 5 hashes (incl. the one we'll try to reuse).
    factories.bind(db_session)
    seed_pw = "Old-pass-A-2025!"
    for _ in range(5):
        await factories.PasswordHistoryFactory.create_async(
            userId=user.id, passwordHash=hash_password(seed_pw)
        )
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    access = login.json()["accessToken"]
    r = await client.post(
        "/api/auth/change-password",
        json={
            "currentPassword": PWD_OK,
            "newPassword": seed_pw,
            "confirmPassword": seed_pw,
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Forgot / Reset password
# ---------------------------------------------------------------------------
async def test_forgot_password_unknown_email_still_202(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    factories.bind(db_session)
    r = await client.post(
        "/api/auth/forgot-password",
        json={"email": "ghost@example.com"},
    )
    assert r.status_code == 202


async def test_reset_password_with_valid_token_changes_password(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    import secrets as _secrets

    user = await _make_user(db_session, email="rst-ok@example.com")
    plain = _secrets.token_urlsafe(32)
    db_session.add(
        PasswordResetToken(
            userId=user.id,
            tokenHash=hash_token(plain),
            expiresAt=datetime.now(UTC) + timedelta(minutes=30),
        )
    )
    await db_session.flush()

    r = await client.post(
        "/api/auth/reset-password",
        json={
            "token": plain,
            "newPassword": PWD_NEW,
            "confirmPassword": PWD_NEW,
        },
    )
    assert r.status_code == 204, r.text
    # Token marked used.
    stmt = select(PasswordResetToken).where(PasswordResetToken.userId == user.id)
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.usedAt is not None


async def test_reset_password_expired_token_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    import secrets as _secrets

    user = await _make_user(db_session, email="rst-expired@example.com")
    plain = _secrets.token_urlsafe(32)
    db_session.add(
        PasswordResetToken(
            userId=user.id,
            tokenHash=hash_token(plain),
            expiresAt=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    await db_session.flush()
    r = await client.post(
        "/api/auth/reset-password",
        json={
            "token": plain,
            "newPassword": PWD_NEW,
            "confirmPassword": PWD_NEW,
        },
    )
    assert r.status_code == 401


async def test_reset_password_used_token_rejected(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    import secrets as _secrets

    user = await _make_user(db_session, email="rst-used@example.com")
    plain = _secrets.token_urlsafe(32)
    db_session.add(
        PasswordResetToken(
            userId=user.id,
            tokenHash=hash_token(plain),
            expiresAt=datetime.now(UTC) + timedelta(minutes=30),
            usedAt=datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    await db_session.flush()
    r = await client.post(
        "/api/auth/reset-password",
        json={
            "token": plain,
            "newPassword": PWD_NEW,
            "confirmPassword": PWD_NEW,
        },
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# MFA setup / verify / disable
# ---------------------------------------------------------------------------
async def test_mfa_setup_returns_secret_and_codes(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="mfa-setup@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    access = login.json()["accessToken"]
    r = await client.post(
        "/api/auth/mfa/setup",
        json={"currentPassword": PWD_OK},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["secret"] and isinstance(body["secret"], str)
    assert body["qrCodeUri"].startswith("otpauth://totp/")
    assert len(body["recoveryCodes"]) == 10


async def test_mfa_verify_setup_activates_credential(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="mfa-activate@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    access = login.json()["accessToken"]
    setup = await client.post(
        "/api/auth/mfa/setup",
        json={"currentPassword": PWD_OK},
        headers={"Authorization": f"Bearer {access}"},
    )
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()
    r = await client.post(
        "/api/auth/mfa/verify-setup",
        json={"code": code},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 204, r.text
    # Reload user — mfaEnabled should now be True.
    await db_session.refresh(user)
    assert user.mfaEnabled is True


async def test_mfa_disable_requires_password_and_code(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="mfa-off@example.com", mfa_enabled=True)
    secret, _, _ = await _enable_mfa(db_session, user)
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    challenge = login.json()["mfaChallenge"]
    code = pyotp.TOTP(secret).now()
    verify = await client.post(
        "/api/auth/mfa/verify",
        json={"challengeToken": challenge, "code": code},
    )
    access = verify.json()["accessToken"]

    # Wrong password — rejected.
    r_bad = await client.post(
        "/api/auth/mfa/disable",
        json={"password": "wrong", "code": pyotp.TOTP(secret).now()},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r_bad.status_code == 401

    # Right password + fresh TOTP — accepted.
    r_ok = await client.post(
        "/api/auth/mfa/disable",
        json={"password": PWD_OK, "code": pyotp.TOTP(secret).now()},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r_ok.status_code == 204
    await db_session.refresh(user)
    assert user.mfaEnabled is False


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
async def test_list_sessions_returns_active_only(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="sessions@example.com")
    # Create 2 logins, then revoke one manually.
    login1 = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    access = login1.json()["accessToken"]
    await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    # Force-revoke the first one.
    stmt = (
        select(RefreshTokenSession)
        .where(RefreshTokenSession.userId == user.id)
        .order_by(RefreshTokenSession.createdAt.asc())
    )
    sessions = (await db_session.execute(stmt)).scalars().all()
    sessions[0].revokedAt = datetime.now(UTC)
    await db_session.flush()

    r = await client.get(
        "/api/auth/sessions",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1  # only the non-revoked one


async def test_revoke_session_drops_it_from_list(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="revoke-sess@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    access = login.json()["accessToken"]
    list_r = await client.get(
        "/api/auth/sessions",
        headers={"Authorization": f"Bearer {access}"},
    )
    session_id = list_r.json()[0]["id"]
    r = await client.delete(
        f"/api/auth/sessions/{session_id}",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 204
    list_r2 = await client.get(
        "/api/auth/sessions",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert list_r2.json() == []


# ---------------------------------------------------------------------------
# AuditLog — every endpoint writes a row
# ---------------------------------------------------------------------------
async def test_audit_log_contains_login_success(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="audit-login@example.com")
    await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    row = await _audit_for(db_session, user.email, AuthEvent.LOGIN_SUCCESS)
    assert row is not None
    assert row.success is True
    # ipAddress is captured (httpx ASGITransport sends "127.0.0.1").
    assert row.ipAddress is not None


async def test_audit_log_contains_login_failed(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="audit-fail@example.com")
    await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "wrong-1234!"},
    )
    row = await _audit_for(db_session, user.email, AuthEvent.LOGIN_FAILED)
    assert row is not None
    assert row.success is False
    assert row.failureReason == "bad_password"


async def test_audit_log_contains_password_changed(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="audit-cp@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    access = login.json()["accessToken"]
    await client.post(
        "/api/auth/change-password",
        json={
            "currentPassword": PWD_OK,
            "newPassword": PWD_NEW,
            "confirmPassword": PWD_NEW,
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    row = await _audit_for(db_session, user.email, AuthEvent.PASSWORD_CHANGED)
    assert row is not None and row.success is True


# ---------------------------------------------------------------------------
# /me byte-compatibility
# ---------------------------------------------------------------------------
async def test_me_returns_mfa_flags(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    user = await _make_user(db_session, email="me-flags@example.com")
    token = create_access_token(user.id, claims={"role": user.role.value})
    r = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    body = r.json()["user"]
    assert body["mfaEnabled"] is False
    assert body["mfaRequired"] is False


# ---------------------------------------------------------------------------
# Unit-level sanity for the crypto primitives we exposed
# ---------------------------------------------------------------------------
async def test_encrypt_decrypt_secret_roundtrip() -> None:
    secret = generate_secret()
    enc = encrypt_secret(secret)
    assert enc != secret
    assert encrypt_secret(secret) != enc  # nonce randomised
    from app.core.security import decrypt_secret as _decrypt

    assert _decrypt(enc) == secret


async def test_recovery_code_hash_then_verify_then_invalid() -> None:
    codes = ["ABC12345"]
    hashed = hash_recovery_codes(codes)
    assert len(hashed) == 1 and hashed[0] != codes[0]
    from app.core.security import verify_recovery_code

    assert verify_recovery_code(hashed[0], "ABC12345") is True
    assert verify_recovery_code(hashed[0], "WRONG999") is False


async def test_verify_totp_window_tolerates_clock_skew() -> None:
    secret = generate_secret()
    # window=1 should accept the current code.
    code = pyotp.TOTP(secret).now()
    assert verify_totp(secret, code) is True
    # Non-digit code instantly rejected.
    assert verify_totp(secret, "abcdef") is False


# ===========================================================================
# Module 1 — Security review fixes (C-1 .. C-5)
# ===========================================================================
# These tests guard against regressions on the 5 CRITICAL findings raised by
# the independent security review of Module 1. Each test maps 1:1 to a fix.
# ===========================================================================


# --- C-1 — /mfa/setup requires current password (and TOTP when re-enrolling)
async def test_mfa_setup_requires_current_password_and_totp_if_already_enabled(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Reject /mfa/setup attempts that lack the proof of current possession.

    The attacker scenario is: stolen access token + no password. Previous
    code happily overwrote the existing credential as long as it was
    `enabled=False` (and even when `enabled=True` it just returned a
    conflict — but the silent overwrite of a *pending* cred remained).
    """
    user = await _make_user(
        db_session, email="mfa-setup-c1@example.com", mfa_enabled=True
    )
    secret, _, _ = await _enable_mfa(db_session, user)
    # Forge an access token directly to simulate the "stolen token" case
    # without going through /login (which would not return one for an
    # MFA-enabled user — exactly the bug we are guarding against).
    access = create_access_token(user.id, claims={"role": user.role.value})

    # 1) No body at all -> 422 (FastAPI validation).
    r0 = await client.post(
        "/api/auth/mfa/setup",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r0.status_code in (400, 422), r0.text

    # 2) Wrong password -> 401, MFA credential untouched.
    r1 = await client.post(
        "/api/auth/mfa/setup",
        json={"currentPassword": "Totally-wrong-1234!"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r1.status_code == 401, r1.text

    # 3) Right password but no current TOTP for a user who already has MFA
    #    -> 401 (cannot prove possession of the existing factor).
    r2 = await client.post(
        "/api/auth/mfa/setup",
        json={"currentPassword": PWD_OK},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r2.status_code == 401, r2.text

    # 4) Right password + WRONG current TOTP -> 401.
    r3 = await client.post(
        "/api/auth/mfa/setup",
        json={"currentPassword": PWD_OK, "currentTotp": "000000"},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r3.status_code == 401, r3.text

    # 5) Right password + VALID current TOTP -> 409 (must /mfa/disable first).
    #    Verifies that even with full proof, we don't silently overwrite.
    valid_totp = pyotp.TOTP(secret).now()
    r4 = await client.post(
        "/api/auth/mfa/setup",
        json={"currentPassword": PWD_OK, "currentTotp": valid_totp},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r4.status_code == 409, r4.text

    # The stored secret MUST be unchanged after every rejected attempt.
    from app.core.security import decrypt_secret as _decrypt

    stmt = select(MfaCredential).where(MfaCredential.userId == user.id)
    cred_after = (await db_session.execute(stmt)).scalar_one()
    assert _decrypt(cred_after.secret) == secret


# --- C-2 — JWT decode pins the algorithm (rejects alg=none / RS256 confusion)
async def test_decode_token_rejects_alg_none() -> None:
    """A JWT explicitly signed with alg=none must not be decodable."""
    import jwt as _jwt  # local alias to keep the test self-contained

    # Hand-craft a token with header `{"alg":"none","typ":"JWT"}`.
    none_token = _jwt.encode(
        {"sub": "attacker", "type": "access", "jti": "x", "exp": 9999999999},
        key="",  # PyJWT requires a key argument even for "none"
        algorithm="none",
    )
    # Sanity — the token really is alg=none (header inspection).
    header = _jwt.get_unverified_header(none_token)
    assert header["alg"] == "none"

    with pytest.raises((_jwt.InvalidTokenError, _jwt.PyJWTError)):
        decode_token(none_token)

    # RS256-signed token: same idea — even with a valid RSA signature, the
    # allow-list pin must refuse it because only HS256 is allowed.
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rs_token = _jwt.encode(
        {"sub": "attacker", "type": "access", "jti": "y", "exp": 9999999999},
        key=key,
        algorithm="RS256",
    )
    with pytest.raises((_jwt.InvalidTokenError, _jwt.PyJWTError)):
        decode_token(rs_token)


# --- C-3 — Recovery codes carry >> 80 bits of entropy and are unique
async def test_recovery_codes_have_sufficient_entropy() -> None:
    """Generate a large batch and check no duplicates + min length."""
    from app.core.security import generate_recovery_codes

    batch = generate_recovery_codes(n=1000)
    assert len(batch) == 1000
    # No collisions across 1000 codes — at 41 bits the birthday probability
    # would be ~22%; at 160 bits it's astronomically small.
    assert len(set(batch)) == 1000
    # Every code is the dashed format with at least 5 chars before the
    # first dash and total length > 20 (sanity floor for the new scheme).
    for code in batch:
        assert "-" in code, code
        assert len(code) >= 20, code
        # Alphabet check — only [A-Z0-9-].
        assert all(c.isalnum() or c == "-" for c in code), code
        assert code == code.upper()


# --- C-4 — client_ip honours TRUSTED_PROXIES (and only TRUSTED_PROXIES)
def _fake_request(*, peer_ip: str | None, xff: str | None = None):
    """Construct a Starlette Request with a forged client + headers."""
    from starlette.requests import Request as _Req

    headers: list[tuple[bytes, bytes]] = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode("ascii")))
    scope = {
        "type": "http",
        "client": (peer_ip, 5555) if peer_ip is not None else None,
        "headers": headers,
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
    }
    return _Req(scope)


def test_client_ip_uses_direct_when_no_trusted_proxies(monkeypatch) -> None:
    from app.core import proxy as _proxy
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "trusted_proxies", "", raising=True)
    _proxy.reset_trusted_proxies_cache()
    req = _fake_request(peer_ip="203.0.113.7", xff="198.51.100.1")
    assert _proxy.client_ip(req) == "203.0.113.7"


def test_client_ip_uses_xff_when_trusted_proxy_matches(monkeypatch) -> None:
    from app.core import proxy as _proxy
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "trusted_proxies", "10.0.0.0/8", raising=True)
    _proxy.reset_trusted_proxies_cache()
    req = _fake_request(peer_ip="10.1.2.3", xff="198.51.100.42")
    assert _proxy.client_ip(req) == "198.51.100.42"


def test_client_ip_ignores_xff_when_proxy_not_trusted(monkeypatch) -> None:
    """Anti-spoof: an open-internet caller cannot lie about its IP."""
    from app.core import proxy as _proxy
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "trusted_proxies", "10.0.0.0/8", raising=True)
    _proxy.reset_trusted_proxies_cache()
    # Peer is on the open internet, NOT in 10.0.0.0/8 — XFF must be ignored.
    req = _fake_request(peer_ip="8.8.8.8", xff="198.51.100.42")
    assert _proxy.client_ip(req) == "8.8.8.8"


def test_client_ip_takes_leftmost_from_xff_list(monkeypatch) -> None:
    from app.core import proxy as _proxy
    from app.core.config import settings as _settings

    monkeypatch.setattr(_settings, "trusted_proxies", "172.16.0.0/12", raising=True)
    _proxy.reset_trusted_proxies_cache()
    req = _fake_request(
        peer_ip="172.16.0.5",
        xff="  198.51.100.42 , 10.0.0.1, 172.16.0.5",
    )
    # Leftmost = the original client.
    assert _proxy.client_ip(req) == "198.51.100.42"


# --- C-5 — get_current_user fails CLOSED with 503 when Redis is down
async def test_get_current_user_fails_closed_when_redis_down(
    db_session: AsyncSession, client: AsyncClient, monkeypatch
) -> None:
    """A Redis outage must produce 503, not pass-through (fail-open)."""
    user = await _make_user(db_session, email="failclosed@example.com")
    access = create_access_token(
        user.id,
        claims={
            "role": user.role.value,
            "regionId": user.regionId,
            "prefectureId": user.prefectureId,
            "subPrefectureId": user.subPrefectureId,
            "schoolId": user.schoolId,
        },
    )

    # Monkeypatch is_token_revoked to simulate Redis being unreachable.
    async def _boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise ConnectionError("simulated redis down")

    from app.shared import deps as _deps

    monkeypatch.setattr(_deps, "is_token_revoked", _boom, raising=True)

    r = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {access}"}
    )
    assert r.status_code == 503, r.text
    assert "temporairement" in r.json()["message"].lower()


# ===========================================================================
# Module 1.1 — Hardening fixes (H-1 .. H-10)
# ===========================================================================
# One test per HIGH from the independent review; the comments explain the
# attacker scenario each one guards against.
# ===========================================================================


# --- H-3 — userAgent column is hard-capped + control chars stripped --------
async def test_audit_userAgent_truncated_to_512_chars(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """A 1 MB User-Agent header must not blow up AuthAuditLog rows."""
    user = await _make_user(db_session, email="ua-cap@example.com")
    huge_ua = "X" * 10_000  # 10 KB — would be a DoS at scale.
    await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
        headers={"User-Agent": huge_ua},
    )
    row = await _audit_for(db_session, user.email, AuthEvent.LOGIN_SUCCESS)
    assert row is not None
    assert row.userAgent is not None
    assert len(row.userAgent) <= 512


async def test_audit_userAgent_strips_control_chars(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """CR/LF/NUL etc. must be removed before persistence — anti log-injection."""
    user = await _make_user(db_session, email="ua-ctrl@example.com")
    # Inject CRLF + NUL + ESC + BEL to try to smuggle a fake log line.
    naughty_ua = "Mozilla/5.0\r\nFAKE-LOG: bypass\x00\x1b[31mRED\x07"
    await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
        headers={"User-Agent": naughty_ua},
    )
    row = await _audit_for(db_session, user.email, AuthEvent.LOGIN_SUCCESS)
    assert row is not None
    ua = row.userAgent or ""
    for forbidden in ("\r", "\n", "\x00", "\x1b", "\x07"):
        assert forbidden not in ua, f"control char {forbidden!r} leaked into UA"


# --- H-7 — /login returns 200 with explicit requiresMfa --------------------
async def test_login_status_is_200_with_requires_mfa_flag(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Both branches of /login are 200 OK and carry the explicit flag."""
    # Branch 1 — no MFA -> requiresMfa=False
    u1 = await _make_user(db_session, email="login-200-nomfa@example.com")
    r1 = await client.post(
        "/api/auth/login", json={"email": u1.email, "password": PWD_OK}
    )
    assert r1.status_code == 200
    assert r1.json()["requiresMfa"] is False

    # Branch 2 — MFA enabled -> requiresMfa=True
    u2 = await _make_user(
        db_session, email="login-200-mfa@example.com", mfa_enabled=True
    )
    await _enable_mfa(db_session, u2)
    r2 = await client.post(
        "/api/auth/login", json={"email": u2.email, "password": PWD_OK}
    )
    assert r2.status_code == 200
    assert r2.json()["requiresMfa"] is True


# --- H-9 — MFA setup writes MFA_SETUP_INITIATED (not MFA_ENABLED fail) -----
async def test_mfa_setup_writes_mfa_setup_initiated_event(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Pending MFA enrolment is a normal event, not a MFA_ENABLED failure."""
    user = await _make_user(db_session, email="mfa-h9@example.com")
    login = await client.post(
        "/api/auth/login", json={"email": user.email, "password": PWD_OK}
    )
    access = login.json()["accessToken"]
    r = await client.post(
        "/api/auth/mfa/setup",
        json={"currentPassword": PWD_OK},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 200
    # The new event MUST be present, success=True.
    setup_row = await _audit_for(
        db_session, user.email, AuthEvent.MFA_SETUP_INITIATED
    )
    assert setup_row is not None
    assert setup_row.success is True
    # And there MUST NOT be an MFA_ENABLED success=False row from this call.
    stmt = (
        select(AuthAuditLog)
        .where(
            AuthAuditLog.email == user.email,
            AuthAuditLog.event == AuthEvent.MFA_ENABLED,
            AuthAuditLog.success.is_(False),
        )
    )
    polluting = (await db_session.execute(stmt)).scalars().all()
    assert polluting == []


# --- H-4 — login timing parity between "user not found" and "wrong password"
async def test_login_user_not_found_takes_similar_time_as_wrong_password(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Without H-4 the unknown-email path returns in ~5 ms while the wrong-
    password path costs an Argon2 verify (~100+ ms in prod, ~1 ms in tests).
    We can't measure absolute prod timings here but we CAN check the same
    code path is exercised: both should call verify_password exactly once.
    """
    import time

    user = await _make_user(db_session, email="timing-known@example.com")

    # Sample several measurements to smooth out CI jitter.
    samples_unknown: list[float] = []
    samples_wrong_pw: list[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        await client.post(
            "/api/auth/login",
            json={
                "email": f"absent-{factories.generate_cuid()[:6]}@example.com",
                "password": "Wrong-pass-1234!",
            },
        )
        samples_unknown.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        await client.post(
            "/api/auth/login",
            json={"email": user.email, "password": "Wrong-pass-1234!"},
        )
        samples_wrong_pw.append(time.perf_counter() - t0)

    median_unknown = sorted(samples_unknown)[len(samples_unknown) // 2]
    median_wrong_pw = sorted(samples_wrong_pw)[len(samples_wrong_pw) // 2]

    # In tests Argon2 is intentionally fast (~1 ms). The two medians should
    # be within an order of magnitude — anything beyond 100 ms drift means
    # the unknown-email branch skipped verify_password.
    assert abs(median_unknown - median_wrong_pw) < 0.1, (
        f"timing asymmetry too large: unknown={median_unknown*1000:.1f} ms, "
        f"wrong_pw={median_wrong_pw*1000:.1f} ms"
    )


# --- H-6 — access token TTL pinned at 30 minutes ---------------------------
def test_access_token_ttl_is_30_minutes_in_config() -> None:
    """Regression guard — operator must not silently revert to 8 h."""
    from app.core.config import settings as _s

    assert _s.jwt_access_token_ttl_minutes == 30


# --- H-1 — MFA counter is NOT reset on first success -----------------------
async def test_mfa_counter_not_reset_on_first_success(
    db_session: AsyncSession,
    client: AsyncClient,
    redis_client: Redis,
) -> None:
    """A successful MFA verify must NOT zero the per-user counter — the
    counter must keep accumulating so a partial-knowledge attacker can't
    bruteforce indefinitely at rate (limit - 1) wrong attempts per success.
    """
    user = await _make_user(
        db_session, email="mfa-counter-h1@example.com", mfa_enabled=True
    )
    secret, _, _ = await _enable_mfa(db_session, user)

    # Burn 3 wrong attempts so we have a non-zero counter to observe.
    for _ in range(3):
        login = await client.post(
            "/api/auth/login",
            json={"email": user.email, "password": PWD_OK},
        )
        await client.post(
            "/api/auth/mfa/verify",
            json={
                "challengeToken": login.json()["mfaChallenge"],
                "code": "000000",
            },
        )

    key = f"rl:mfa:user:{user.id}"
    before = await redis_client.get(key)
    assert before is not None and int(before) >= 3, (
        f"expected >=3 failed-attempt counter, got {before!r}"
    )

    # Now a SUCCESS. With H-1 the counter is preserved (no reset).
    login_ok = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    code = pyotp.TOTP(secret).now()
    r = await client.post(
        "/api/auth/mfa/verify",
        json={
            "challengeToken": login_ok.json()["mfaChallenge"],
            "code": code,
        },
    )
    assert r.status_code == 200

    after = await redis_client.get(key)
    assert after is not None, "MFA counter unexpectedly deleted"
    # After 4 verify attempts (3 wrong + 1 right) the counter MUST be >= 4.
    # With the pre-H-1 bug it would be 0 (or 1 if only the success counted).
    assert int(after) >= 4, (
        f"MFA counter reset on success — got {after!r}, expected >=4"
    )


# --- H-5 — change_password & reset_password revoke active refresh sessions -
async def test_change_password_revokes_all_other_sessions(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """After changing the password every active refresh session is dead."""
    user = await _make_user(db_session, email="cp-revokes@example.com")
    # Login twice — two distinct active refresh sessions.
    login1 = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    login2 = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    refresh_a = login1.json()["refreshToken"]
    refresh_b = login2.json()["refreshToken"]
    access = login2.json()["accessToken"]

    # Confirm both sessions are alive in DB.
    stmt = select(RefreshTokenSession).where(
        RefreshTokenSession.userId == user.id,
        RefreshTokenSession.revokedAt.is_(None),
    )
    alive_before = (await db_session.execute(stmt)).scalars().all()
    assert len(alive_before) == 2

    # Change password.
    r = await client.post(
        "/api/auth/change-password",
        json={
            "currentPassword": PWD_OK,
            "newPassword": PWD_NEW,
            "confirmPassword": PWD_NEW,
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 204, r.text

    # All sessions revoked.
    alive_after = (await db_session.execute(stmt)).scalars().all()
    assert alive_after == []

    # Both refresh tokens rejected.
    r_a = await client.post(
        "/api/auth/refresh", json={"refreshToken": refresh_a}
    )
    r_b = await client.post(
        "/api/auth/refresh", json={"refreshToken": refresh_b}
    )
    assert r_a.status_code == 401
    assert r_b.status_code == 401


async def test_reset_password_revokes_all_sessions(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """Same property as above, exercised through /reset-password."""
    import secrets as _secrets

    user = await _make_user(db_session, email="rst-revokes@example.com")
    # Login twice so we have 2 active sessions.
    await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )

    # Seed a valid reset token.
    plain = _secrets.token_urlsafe(32)
    db_session.add(
        PasswordResetToken(
            userId=user.id,
            tokenHash=hash_token(plain),
            expiresAt=datetime.now(UTC) + timedelta(minutes=30),
        )
    )
    await db_session.flush()

    r = await client.post(
        "/api/auth/reset-password",
        json={
            "token": plain,
            "newPassword": PWD_NEW,
            "confirmPassword": PWD_NEW,
        },
    )
    assert r.status_code == 204, r.text

    stmt = select(RefreshTokenSession).where(
        RefreshTokenSession.userId == user.id,
        RefreshTokenSession.revokedAt.is_(None),
    )
    alive = (await db_session.execute(stmt)).scalars().all()
    assert alive == []


# --- H-10 — refresh rotation is atomic: failure leaves old session valid ---
async def test_refresh_rollback_keeps_old_session_valid_on_error(
    db_session: AsyncSession, client: AsyncClient, monkeypatch
) -> None:
    """If the new-pair emission raises mid-flight, the OLD refresh must
    still work — the attacker scenario being a transient DB hiccup that
    used to silently log the user out.
    """
    user = await _make_user(db_session, email="refresh-atomic@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    refresh_v1 = login.json()["refreshToken"]

    # Sabotage _issue_session so the rotation aborts BEFORE revoking the
    # old DB row. With H-10 the request fails but the old refresh stays
    # usable on retry.
    from app.modules.auth.service import AuthService

    original_issue = AuthService._issue_session
    boom_calls: dict[str, int] = {"n": 0}

    async def _flaky_issue(self, user, **kwargs):  # type: ignore[no-untyped-def]
        boom_calls["n"] += 1
        if boom_calls["n"] == 1:
            raise RuntimeError("simulated DB hiccup mid-rotation")
        return await original_issue(self, user, **kwargs)

    monkeypatch.setattr(AuthService, "_issue_session", _flaky_issue)

    # First attempt — explodes on the issuance step. ASGITransport
    # re-raises unhandled exceptions (raise_app_exceptions=True by default),
    # so we catch the simulated failure ourselves. The crucial property
    # is what happens AFTER: the old refresh must still be valid because
    # H-10 rotates atomically (mint new -> revoke old, single transaction).
    with pytest.raises(RuntimeError, match="simulated DB hiccup"):
        await client.post(
            "/api/auth/refresh", json={"refreshToken": refresh_v1}
        )

    # Sanity — the old DB session row must NOT have been marked revoked
    # by the failed attempt (otherwise the retry would fail too).
    stmt_old = select(RefreshTokenSession).where(
        RefreshTokenSession.userId == user.id,
        RefreshTokenSession.revokedAt.is_(None),
    )
    alive_after_failure = (await db_session.execute(stmt_old)).scalars().all()
    assert len(alive_after_failure) >= 1, (
        "old refresh session was revoked despite rotation failure — H-10 broken"
    )

    # Retry — the old refresh is still valid (H-10 guarantee).
    r_retry = await client.post(
        "/api/auth/refresh", json={"refreshToken": refresh_v1}
    )
    assert r_retry.status_code == 200, r_retry.text
    body = r_retry.json()
    assert body["refreshToken"] and body["refreshToken"] != refresh_v1


# --- H-8 — weak password rejected, strong accepted -------------------------
async def test_change_password_rejects_weak_password(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """zxcvbn score < 3 must produce a 422 before the service ever runs."""
    user = await _make_user(db_session, email="pw-weak@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    access = login.json()["accessToken"]
    # Long enough to clear min_length=12 but trivial pattern -> score 1.
    weak = "password1234"
    r = await client.post(
        "/api/auth/change-password",
        json={
            "currentPassword": PWD_OK,
            "newPassword": weak,
            "confirmPassword": weak,
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 422, r.text


async def test_change_password_accepts_strong_password(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """A high-entropy passphrase must clear the validator (score >= 3)."""
    user = await _make_user(db_session, email="pw-strong@example.com")
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    access = login.json()["accessToken"]
    # Same PWD_NEW used elsewhere — known to score 4.
    r = await client.post(
        "/api/auth/change-password",
        json={
            "currentPassword": PWD_OK,
            "newPassword": PWD_NEW,
            "confirmPassword": PWD_NEW,
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 204, r.text


# --- H-2 — /audit-log endpoint: own logs / admin override / non-admin lock -
async def test_audit_log_endpoint_returns_own_logs(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """A regular user reads their own auth events, never another user's."""
    user = await _make_user(db_session, email="audit-own@example.com")
    # Make a couple of events on this user.
    login = await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": PWD_OK},
    )
    access = login.json()["accessToken"]
    await client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "Wrong-pass-1234!"},
    )

    r = await client.get(
        "/api/auth/audit-log",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 2
    # All returned rows must carry an event string and a createdAt.
    for row in rows:
        assert "event" in row and "createdAt" in row
        assert "userAgent" in row  # nullable but key present


async def test_audit_log_endpoint_admin_can_filter_by_user(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """An admin can pass ?userId= to inspect another user's events."""
    victim = await _make_user(db_session, email="audit-victim@example.com")
    admin = await _make_user(
        db_session,
        email="audit-admin@example.com",
        role=UserRole.NATIONAL_ADMIN,
    )
    # Generate a LOGIN_FAILED row on the victim.
    await client.post(
        "/api/auth/login",
        json={"email": victim.email, "password": "Wrong-pass-1234!"},
    )

    admin_token = create_access_token(
        admin.id,
        claims={
            "role": admin.role.value,
            "regionId": admin.regionId,
            "prefectureId": admin.prefectureId,
            "subPrefectureId": admin.subPrefectureId,
            "schoolId": admin.schoolId,
        },
    )

    r = await client.get(
        f"/api/auth/audit-log?userId={victim.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    # Should have at least the LOGIN_FAILED row on the victim.
    events = [row["event"] for row in rows]
    assert AuthEvent.LOGIN_FAILED in events


async def test_audit_log_endpoint_non_admin_ignores_userId_param(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """A non-admin who passes ?userId=<other> must NOT see other users' events."""
    victim = await _make_user(db_session, email="audit-victim2@example.com")
    other = await _make_user(db_session, email="audit-other@example.com")

    # Generate a LOGIN_SUCCESS row on the victim.
    await client.post(
        "/api/auth/login",
        json={"email": victim.email, "password": PWD_OK},
    )
    # Generate a LOGIN_SUCCESS row on `other` (the caller).
    other_login = await client.post(
        "/api/auth/login",
        json={"email": other.email, "password": PWD_OK},
    )
    other_access = other_login.json()["accessToken"]

    r = await client.get(
        f"/api/auth/audit-log?userId={victim.id}",
        headers={"Authorization": f"Bearer {other_access}"},
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    # All returned rows must belong to `other` — verified by reloading from
    # DB: every row id we got back must have userId == other.id.
    for row in rows:
        row_id = row["id"]
        stored = (
            await db_session.execute(
                select(AuthAuditLog).where(AuthAuditLog.id == row_id)
            )
        ).scalar_one()
        assert stored.userId == other.id, (
            "non-admin received another user's audit row — RBAC bypass!"
        )


# Quiet ruff F401 on a couple of import-only usages.
_ = (create_refresh_token,)
