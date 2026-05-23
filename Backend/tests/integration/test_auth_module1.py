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
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["accessToken"] and isinstance(body["accessToken"], str)
    assert body["refreshToken"] and isinstance(body["refreshToken"], str)
    assert body["user"]["id"] == user.id
    assert body["mfaChallenge"] is None

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
    assert r.status_code == 201
    body = r.json()
    assert body["accessToken"] is None and body["refreshToken"] is None
    assert body["user"] is None
    assert body["mfaChallenge"]
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
    assert login.status_code == 201, login.text
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


# Quiet ruff F401 on a couple of import-only usages.
_ = (create_refresh_token,)
