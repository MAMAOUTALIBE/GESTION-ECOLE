"""Auth service — login / MFA / refresh / logout / password lifecycle / sessions.

Module 1 hardening is centralised here. Every public method:
* Looks up the user (case-insensitive on email) with selectinload territorial entities.
* Returns a typed schema (LoginResponse / MfaSetupResponse / SessionInfo / None).
* Writes a row in AuthAuditLog with the IP + UA + outcome of the operation.
* For the login path, also feeds Prometheus via `auth_login_total`.

The router is the thin layer that injects HTTP context (Request, Redis client,
DbSession) and translates `AppError`s into JSON responses.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from loguru import logger
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    ValidationFailedError,
)
from app.core.observability import auth_login_total
from app.core.rate_limit import (
    check_login_attempt,
    check_mfa_attempt,
    check_password_reset_request,
    reset_login_counters,
)
from app.core.security import (
    create_access_token,
    create_mfa_challenge_token,
    create_refresh_token,
    decode_token,
    decrypt_secret,
    encrypt_secret,
    hash_password,
    hash_token,
    is_token_revoked,
    needs_rehash,
    revoke_token,
    verify_password,
)
from app.modules.auth.mfa import (
    consume_recovery_code,
    fresh_recovery_codes,
    generate_secret,
    provisioning_uri,
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
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LoginUser,
    MeResponse,
    MeUser,
    MfaSetupResponse,
    SessionInfo,
)
from app.shared.enums import UserRole

INVALID_CREDENTIALS_MESSAGE = "Identifiants invalides"
PASSWORD_HISTORY_DEPTH = 5
PASSWORD_RESET_TTL_MIN = 30

# Module 1.1 — H-3 — hard caps on free-form audit columns. Without these
# bounds a malicious client could write 1 MB user-agent headers (DoS on the
# Postgres heap) or inject control characters into a log aggregator.
_AUDIT_USER_AGENT_MAX = 512
_AUDIT_EMAIL_MAX = 320  # RFC 5321 — 64 (local) + @ + 255 (domain) = 320 max
_AUDIT_REASON_MAX = 200
# Allow tab (\x09) so multi-line UAs can keep some structure, but drop every
# other C0 control character (incl. NUL, BEL, BS, VT, FF, CR, LF, ESC...).
_CONTROL_CHARS_RE = "".join(chr(c) for c in range(0x00, 0x20) if c != 0x09) + "\x7f"


def _sanitize_audit_string(value: str | None, *, max_length: int) -> str | None:
    """Truncate + strip control characters from a free-form audit string.

    Returns None unchanged (no surprises for callers). Empty string after
    stripping is also returned as-is — we want auditable evidence that the
    header was present but contained only junk.
    """
    if value is None:
        return None
    cleaned = value.translate({ord(c): None for c in _CONTROL_CHARS_RE})
    return cleaned[:max_length]


# Module 1.1 — H-4 — pre-computed dummy Argon2 hash used to equalise the time
# of a "user not found" login with the time of a "wrong password" login. We
# compute it once at import (using the production-cost PasswordHasher) so the
# very first request after a cold start doesn't pay a 200 ms surprise tax.
def _build_dummy_argon2_hash() -> str:
    """Compute a fixed Argon2 hash used solely for timing equalisation.

    Picked to be deterministic across processes so debugging is predictable;
    the *content* is irrelevant (the hash itself is what we feed to
    `verify_password` on the unhappy login path).
    """
    from app.core.security import hash_password as _hp

    return _hp("__timing_attack_dummy_password_module_1_1__")


_DUMMY_ARGON2_HASH: str = _build_dummy_argon2_hash()


class AuthService:
    """Stateless service — instances are created per request via Depends."""

    def __init__(self, session: AsyncSession, redis: Any = None) -> None:
        self.session = session
        self.redis = redis  # `redis.asyncio.Redis | None` (None in legacy paths)

    # =========================================================================
    # /me — unchanged byte-compatible
    # =========================================================================
    @staticmethod
    def me(user: User) -> MeResponse:
        """Return the authenticated user's profile (no nested objects)."""
        return MeResponse(user=MeUser.model_validate(user))

    # =========================================================================
    # /login — issues access+refresh OR an MFA challenge
    # =========================================================================
    async def login(
        self,
        dto: LoginRequest,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> LoginResponse:
        normalized_email = dto.email.lower().strip()

        # Rate-limit FIRST so brute forcers can't even reach the Argon2 cost.
        if self.redis is not None:
            by_email, by_ip = await check_login_attempt(
                self.redis, normalized_email, ip_address or ""
            )
            if not (by_email.allowed and by_ip.allowed):
                await self._audit(
                    event=AuthEvent.RATE_LIMITED,
                    email=normalized_email,
                    ip=ip_address,
                    ua=user_agent,
                    success=False,
                    reason="login_rate_limited",
                )
                auth_login_total.labels(result="rate_limited").inc()
                raise RateLimitedError(
                    detail="Trop de tentatives. Réessayez plus tard.",
                )

        user = await self._load_user_by_email(normalized_email)

        if user is None or not user.isActive:
            # Module 1.1 — H-4 — burn the Argon2 cost on a dummy hash so the
            # response time is indistinguishable from a "wrong password" answer.
            # Without this an attacker can enumerate registered emails by
            # measuring response latency (Argon2 verify > 100 ms vs DB-miss ~5 ms).
            verify_password(dto.password, _DUMMY_ARGON2_HASH)
            auth_login_total.labels(
                result="inactive" if user is not None else "invalid"
            ).inc()
            await self._audit(
                event=AuthEvent.LOGIN_FAILED,
                email=normalized_email,
                ip=ip_address,
                ua=user_agent,
                success=False,
                reason="unknown_or_inactive",
                user_id=user.id if user is not None else None,
            )
            raise UnauthorizedError(detail=INVALID_CREDENTIALS_MESSAGE)

        if not verify_password(dto.password, user.passwordHash):
            auth_login_total.labels(result="invalid").inc()
            await self._audit(
                event=AuthEvent.LOGIN_FAILED,
                email=normalized_email,
                ip=ip_address,
                ua=user_agent,
                success=False,
                reason="bad_password",
                user_id=user.id,
            )
            raise UnauthorizedError(detail=INVALID_CREDENTIALS_MESSAGE)

        # Migrate legacy bcrypt hashes to Argon2 transparently.
        if needs_rehash(user.passwordHash):
            user.passwordHash = hash_password(dto.password)
            await self.session.flush()

        # MFA gate ------------------------------------------------------------
        if user.mfaEnabled:
            challenge = create_mfa_challenge_token(user.id)
            await self._audit(
                event=AuthEvent.LOGIN_SUCCESS,
                user_id=user.id,
                email=normalized_email,
                ip=ip_address,
                ua=user_agent,
                success=True,
                reason="mfa_challenge_issued",
            )
            auth_login_total.labels(result="mfa_required").inc()
            return LoginResponse(
                accessToken=None,
                refreshToken=None,
                user=None,
                mfaChallenge=challenge,
            )

        # No MFA — issue tokens immediately.
        auth_login_total.labels(result="success").inc()
        return await self._issue_session(
            user, ip_address=ip_address, user_agent=user_agent
        )

    # =========================================================================
    # /mfa/verify — exchanges the challenge token for access+refresh
    # =========================================================================
    async def verify_mfa(
        self,
        challenge_token: str,
        code: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> LoginResponse:
        try:
            payload = decode_token(challenge_token, expected_type="mfa_challenge")
        except jwt.PyJWTError as exc:
            raise UnauthorizedError(detail="Challenge invalide ou expiré") from exc

        user_id = payload.get("sub")
        if not user_id:
            raise UnauthorizedError(detail="Challenge invalide")

        # Per-user MFA rate limit.
        if self.redis is not None:
            rl = await check_mfa_attempt(self.redis, user_id)
            if not rl.allowed:
                await self._audit(
                    event=AuthEvent.RATE_LIMITED,
                    user_id=user_id,
                    ip=ip_address,
                    ua=user_agent,
                    success=False,
                    reason="mfa_rate_limited",
                )
                raise RateLimitedError(detail="Trop de tentatives MFA.")

        user = await self._load_user_by_id(user_id)
        if user is None or not user.isActive or not user.mfaEnabled:
            raise UnauthorizedError(detail="Compte invalide pour MFA")

        cred = await self._get_mfa_credential(user_id)
        if cred is None or not cred.enabled:
            raise UnauthorizedError(detail="MFA non configuré")

        # Try TOTP first, then recovery code.
        secret_plain = decrypt_secret(cred.secret)
        ok = verify_totp(secret_plain, code)
        consumed_recovery = False
        if not ok:
            matched, updated = consume_recovery_code(
                list(cred.recoveryCodesHashed or []), code
            )
            if matched:
                cred.recoveryCodesHashed = updated
                await self.session.flush()
                ok = True
                consumed_recovery = True

        if not ok:
            await self._audit(
                event=AuthEvent.MFA_FAILED,
                user_id=user_id,
                email=user.email,
                ip=ip_address,
                ua=user_agent,
                success=False,
                reason="bad_code",
            )
            raise UnauthorizedError(detail="Code MFA invalide")

        # Success — revoke the challenge JTI.
        #
        # Module 1.1 — H-1 — we DELIBERATELY do NOT reset the MFA counter
        # here. Resetting on first-success let an attacker who had partial
        # knowledge (e.g. recovered TOTP seed from a sync log) bruteforce
        # the second MFA stage essentially forever: 9 wrong + 1 right per
        # 15 min window. The counter now expires naturally with the TTL
        # (15 min) — see app.core.rate_limit.MFA_WINDOW_S. We accept a tiny
        # UX hit (legitimate user who fat-fingered earlier waits up to
        # 15 min if they reach the limit) in exchange for closing this
        # bruteforce window.
        if self.redis is not None:
            import contextlib

            with contextlib.suppress(KeyError):  # challenge always has jti in practice
                await revoke_token(self.redis, payload["jti"], int(payload["exp"]))

        await self._audit(
            event=AuthEvent.MFA_SUCCESS,
            user_id=user_id,
            email=user.email,
            ip=ip_address,
            ua=user_agent,
            success=True,
            reason="recovery_code" if consumed_recovery else "totp",
        )
        return await self._issue_session(
            user, ip_address=ip_address, user_agent=user_agent
        )

    # =========================================================================
    # /refresh — rotate refresh token + issue new access
    # =========================================================================
    async def refresh(
        self,
        refresh_token: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> LoginResponse:
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError(detail="Refresh expiré") from exc
        except jwt.PyJWTError as exc:
            raise UnauthorizedError(detail="Refresh invalide") from exc

        jti = payload.get("jti")
        user_id = payload.get("sub")
        if not jti or not user_id:
            raise UnauthorizedError(detail="Refresh invalide")

        # Redis blacklist check — fast path.
        if self.redis is not None and await is_token_revoked(self.redis, jti):
            raise UnauthorizedError(detail="Refresh révoqué")

        # DB session lookup — source of truth.
        token_h = hash_token(refresh_token)
        session_row = await self._find_session_by_hash(token_h)
        if session_row is None or session_row.revokedAt is not None:
            raise UnauthorizedError(detail="Refresh révoqué")

        now = datetime.now(UTC)
        if session_row.expiresAt < now:
            raise UnauthorizedError(detail="Refresh expiré")

        user = await self._load_user_by_id(user_id)
        if user is None or not user.isActive:
            raise UnauthorizedError(detail="Compte invalide")

        # Module 1.1 — H-10 — ATOMIC rotation.
        #
        # Old order: revoke old DB row + Redis JTI -> mint new pair. If the
        # mint step or its DB insert raised, the user had no working refresh
        # left and got silently logged out (the previous refresh was now in
        # the blacklist). The new sequence is:
        #   1. Mint the new (access, refresh) pair via _issue_session, which
        #      flushes the new RefreshTokenSession row inside the same
        #      AsyncSession (no autocommit — conftest wraps it in a savepoint
        #      anyway).
        #   2. Mark the old session row revoked + add the JTI to Redis.
        #   3. Final flush to persist the revocation alongside the new row.
        # If anything in step 1 raises, the SQLAlchemy session is rolled back
        # by the FastAPI dep / Db middleware, leaving the OLD refresh valid
        # for the client to retry. We deliberately do NOT touch Redis before
        # step 1 succeeds, so a transient DB hiccup never turns into a forced
        # logout.
        try:
            new_response = await self._issue_session(
                user, ip_address=ip_address, user_agent=user_agent
            )
        except Exception:
            # Bubble up — caller / FastAPI exception handler rolls back the
            # session. The OLD session_row is unchanged (we haven't touched
            # it yet) so the client's existing refresh token stays usable.
            raise

        # New pair is in DB; now invalidate the old one.
        session_row.revokedAt = now
        session_row.revokedReason = "rotated"
        if self.redis is not None:
            await revoke_token(self.redis, jti, int(payload["exp"]))
        await self.session.flush()

        await self._audit(
            event=AuthEvent.REFRESH,
            user_id=user.id,
            email=user.email,
            ip=ip_address,
            ua=user_agent,
            success=True,
        )
        return new_response

    # =========================================================================
    # /logout — revoke access (best effort) + refresh (DB row + Redis JTI)
    # =========================================================================
    async def logout(
        self,
        *,
        access_token: str | None,
        refresh_token: str | None,
        user: User | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        revoked_any = False

        if access_token:
            try:
                payload = decode_token(access_token, expected_type="access")
                if self.redis is not None:
                    await revoke_token(
                        self.redis, payload["jti"], int(payload["exp"])
                    )
                revoked_any = True
            except (jwt.PyJWTError, KeyError):
                pass

        if refresh_token:
            try:
                payload = decode_token(refresh_token, expected_type="refresh")
                if self.redis is not None:
                    await revoke_token(
                        self.redis, payload["jti"], int(payload["exp"])
                    )
                token_h = hash_token(refresh_token)
                session_row = await self._find_session_by_hash(token_h)
                if session_row is not None and session_row.revokedAt is None:
                    session_row.revokedAt = datetime.now(UTC)
                    session_row.revokedReason = "logout"
                    await self.session.flush()
                revoked_any = True
            except (jwt.PyJWTError, KeyError):
                pass

        await self._audit(
            event=AuthEvent.LOGOUT,
            user_id=user.id if user is not None else None,
            email=user.email if user is not None else None,
            ip=ip_address,
            ua=user_agent,
            success=revoked_any,
        )

    # =========================================================================
    # /change-password — vérifie current + historique 5
    # =========================================================================
    async def change_password(
        self,
        user: User,
        current: str,
        new: str,
        confirm: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        if new != confirm:
            raise ValidationFailedError(detail="Les nouveaux mots de passe diffèrent")
        if new == current:
            raise ValidationFailedError(detail="Le nouveau doit différer du courant")
        if not verify_password(current, user.passwordHash):
            await self._audit(
                event=AuthEvent.PASSWORD_CHANGED,
                user_id=user.id,
                email=user.email,
                ip=ip_address,
                ua=user_agent,
                success=False,
                reason="bad_current",
            )
            raise UnauthorizedError(detail=INVALID_CREDENTIALS_MESSAGE)

        # Forbid reusing the current hash or any of the N most recent ones.
        history = await self._password_history(user.id, PASSWORD_HISTORY_DEPTH)
        all_hashes = [user.passwordHash, *(h.passwordHash for h in history)]
        if any(verify_password(new, h) for h in all_hashes):
            await self._audit(
                event=AuthEvent.PASSWORD_CHANGED,
                user_id=user.id,
                email=user.email,
                ip=ip_address,
                ua=user_agent,
                success=False,
                reason="history_reuse",
            )
            raise ConflictError(
                detail=f"Mot de passe déjà utilisé (historique {PASSWORD_HISTORY_DEPTH})",
            )

        # Rotate: push the OLD hash into history, then set the new one.
        self.session.add(
            PasswordHistory(userId=user.id, passwordHash=user.passwordHash)
        )
        user.passwordHash = hash_password(new)
        user.passwordChangedAt = datetime.now(UTC)
        # Module 1.1 — H-5 — invalidate every active refresh session so a
        # stolen-but-still-valid refresh token cannot mint a fresh access
        # token after the user changed their password. Access tokens (no DB
        # row) are NOT invalidated here — H-6 caps their TTL at 30 min,
        # which is the residual exposure window.
        await self.session.execute(
            update(RefreshTokenSession)
            .where(
                RefreshTokenSession.userId == user.id,
                RefreshTokenSession.revokedAt.is_(None),
            )
            .values(
                revokedAt=datetime.now(UTC),
                revokedReason="password_changed",
            )
        )
        await self.session.flush()

        await self._audit(
            event=AuthEvent.PASSWORD_CHANGED,
            user_id=user.id,
            email=user.email,
            ip=ip_address,
            ua=user_agent,
            success=True,
        )

    # =========================================================================
    # /forgot-password — emit a single-use token (NEVER leak email existence)
    # =========================================================================
    async def forgot_password(
        self,
        email: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> str | None:
        """Return the **plain** reset token (caller emails it). None when the
        request was throttled OR the email doesn't match anyone — same code
        path so timing can't be used as an oracle.
        """
        normalized = email.lower().strip()

        if self.redis is not None:
            by_email, by_ip = await check_password_reset_request(
                self.redis, normalized, ip_address or ""
            )
            if not (by_email.allowed and by_ip.allowed):
                await self._audit(
                    event=AuthEvent.RATE_LIMITED,
                    email=normalized,
                    ip=ip_address,
                    ua=user_agent,
                    success=False,
                    reason="forgot_password_rate_limited",
                )
                return None

        user = await self._load_user_by_email(normalized)
        await self._audit(
            event=AuthEvent.PASSWORD_RESET_REQUESTED,
            email=normalized,
            user_id=user.id if user is not None else None,
            ip=ip_address,
            ua=user_agent,
            success=user is not None,
            reason=None if user is not None else "unknown_email",
        )
        if user is None:
            return None

        # Generate an opaque 256-bit token. Persist its SHA-256 hash only.
        import secrets as _secrets

        token = _secrets.token_urlsafe(32)
        self.session.add(
            PasswordResetToken(
                userId=user.id,
                tokenHash=hash_token(token),
                expiresAt=datetime.now(UTC) + timedelta(minutes=PASSWORD_RESET_TTL_MIN),
                ipAddress=ip_address,
            )
        )
        await self.session.flush()
        return token

    # =========================================================================
    # /reset-password — consumes a single-use token
    # =========================================================================
    async def reset_password(
        self,
        token: str,
        new_password: str,
        confirm: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        if new_password != confirm:
            raise ValidationFailedError(detail="Les nouveaux mots de passe diffèrent")

        token_h = hash_token(token)
        stmt = select(PasswordResetToken).where(PasswordResetToken.tokenHash == token_h)
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        now = datetime.now(UTC)
        if row is None or row.usedAt is not None or row.expiresAt < now:
            await self._audit(
                event=AuthEvent.PASSWORD_RESET_USED,
                ip=ip_address,
                ua=user_agent,
                success=False,
                reason="invalid_or_expired",
            )
            raise UnauthorizedError(detail="Lien invalide ou expiré")

        user = await self._load_user_by_id(row.userId)
        if user is None:
            raise UnauthorizedError(detail="Utilisateur inconnu")

        # Don't allow setting a reset password equal to one in the history.
        history = await self._password_history(user.id, PASSWORD_HISTORY_DEPTH)
        all_hashes = [user.passwordHash, *(h.passwordHash for h in history)]
        if any(verify_password(new_password, h) for h in all_hashes):
            raise ConflictError(
                detail=f"Mot de passe déjà utilisé (historique {PASSWORD_HISTORY_DEPTH})",
            )

        # Commit: archive old hash, set new, mark token used.
        self.session.add(
            PasswordHistory(userId=user.id, passwordHash=user.passwordHash)
        )
        user.passwordHash = hash_password(new_password)
        user.passwordChangedAt = now
        row.usedAt = now
        # Module 1.1 — H-5 — same logic as change_password: a password reset
        # is by definition a security event, so every active refresh session
        # for this user is revoked. The user must /login again after reset.
        await self.session.execute(
            update(RefreshTokenSession)
            .where(
                RefreshTokenSession.userId == user.id,
                RefreshTokenSession.revokedAt.is_(None),
            )
            .values(
                revokedAt=now,
                revokedReason="password_reset",
            )
        )
        await self.session.flush()

        await self._audit(
            event=AuthEvent.PASSWORD_RESET_USED,
            user_id=user.id,
            email=user.email,
            ip=ip_address,
            ua=user_agent,
            success=True,
        )

    # =========================================================================
    # /mfa/setup, /mfa/verify-setup, /mfa/disable
    # =========================================================================
    async def setup_mfa(
        self,
        user: User,
        *,
        current_password: str,
        current_totp: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> MfaSetupResponse:
        """Security fix C-1 — protect MFA enrollment.

        Without these checks, a stolen access token was enough to call
        ``/mfa/setup`` and silently overwrite the victim's MFA credential
        (the previous code only refused when ``cred.enabled is True``; a
        cred in pending state was happily clobbered, effectively giving
        the attacker a fresh secret). We now require:

        1. ``current_password`` — re-verified via ``verify_password``.
           Refused with 401 if it does not match — no information leak.
        2. ``current_totp`` — required only when the user already has
           ``mfaEnabled=True``. Must be a valid TOTP or recovery code on
           the existing enabled credential. This blocks "lost my phone +
           stolen token" combo attacks.
        3. The existing "MFA déjà activé" conflict is **kept** so the
           normal flow asks the user to call ``/mfa/disable`` first.
        """
        if not verify_password(current_password, user.passwordHash):
            await self._audit(
                event=AuthEvent.MFA_ENABLED,
                user_id=user.id,
                email=user.email,
                ip=ip_address,
                ua=user_agent,
                success=False,
                reason="setup_bad_password",
            )
            raise UnauthorizedError(detail=INVALID_CREDENTIALS_MESSAGE)

        cred = await self._get_mfa_credential(user.id)
        if user.mfaEnabled or (cred is not None and cred.enabled):
            # User already has working MFA — they must prove they still
            # control the existing factor before we replace it.
            if not current_totp:
                await self._audit(
                    event=AuthEvent.MFA_ENABLED,
                    user_id=user.id,
                    email=user.email,
                    ip=ip_address,
                    ua=user_agent,
                    success=False,
                    reason="setup_missing_totp",
                )
                raise UnauthorizedError(
                    detail="Code MFA actuel requis pour ré-enrôler",
                )
            existing = cred  # cred is guaranteed non-None when mfaEnabled True
            if existing is None:
                # Defensive: mfaEnabled=True but no credential row -> data
                # inconsistency. Refuse loudly rather than silently allow.
                raise UnauthorizedError(detail="État MFA incohérent")
            secret_plain = decrypt_secret(existing.secret)
            ok = verify_totp(secret_plain, current_totp)
            if not ok:
                matched, updated = consume_recovery_code(
                    list(existing.recoveryCodesHashed or []), current_totp
                )
                if matched:
                    existing.recoveryCodesHashed = updated
                    ok = True
            if not ok:
                await self._audit(
                    event=AuthEvent.MFA_FAILED,
                    user_id=user.id,
                    email=user.email,
                    ip=ip_address,
                    ua=user_agent,
                    success=False,
                    reason="setup_bad_totp",
                )
                raise UnauthorizedError(detail="Code MFA invalide")
            # All checks passed — keep the original ConflictError contract
            # so the API still tells the caller they need to /mfa/disable
            # first (avoids accidental silent overwrite during normal use).
            raise ConflictError(detail="MFA déjà activé")

        secret = generate_secret()
        plain_codes, hashed_codes = fresh_recovery_codes()

        if cred is None:
            cred = MfaCredential(
                userId=user.id,
                secret=encrypt_secret(secret),
                enabled=False,
                recoveryCodesHashed=hashed_codes,
            )
            self.session.add(cred)
        else:
            cred.secret = encrypt_secret(secret)
            cred.recoveryCodesHashed = hashed_codes
            cred.enabled = False
            cred.verifiedAt = None
        await self.session.flush()

        # Module 1.1 — H-9 — write an explicit MFA_SETUP_INITIATED event
        # (success=True). The previous code wrote MFA_ENABLED with
        # success=False at this point, which polluted the "MFA enrolment
        # failure" dashboard with what is actually a normal happy-path step
        # (the user just kicked off the QR-scan flow and will confirm via
        # /mfa/verify-setup). MFA_ENABLED success=False is now reserved for
        # actual enrolment failures (bad password, missing TOTP, etc.).
        await self._audit(
            event=AuthEvent.MFA_SETUP_INITIATED,
            user_id=user.id,
            email=user.email,
            ip=ip_address,
            ua=user_agent,
            success=True,
            reason="setup_pending",
        )
        return MfaSetupResponse(
            secret=secret,
            qrCodeUri=provisioning_uri(user.email, secret),
            recoveryCodes=plain_codes,
        )

    async def verify_mfa_setup(
        self,
        user: User,
        code: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        cred = await self._get_mfa_credential(user.id)
        if cred is None:
            raise NotFoundError(detail="Aucune configuration MFA en attente")

        secret_plain = decrypt_secret(cred.secret)
        if not verify_totp(secret_plain, code):
            await self._audit(
                event=AuthEvent.MFA_FAILED,
                user_id=user.id,
                email=user.email,
                ip=ip_address,
                ua=user_agent,
                success=False,
                reason="bad_code_setup",
            )
            raise UnauthorizedError(detail="Code MFA invalide")

        cred.enabled = True
        cred.verifiedAt = datetime.now(UTC)
        user.mfaEnabled = True
        await self.session.flush()

        await self._audit(
            event=AuthEvent.MFA_ENABLED,
            user_id=user.id,
            email=user.email,
            ip=ip_address,
            ua=user_agent,
            success=True,
        )

    async def disable_mfa(
        self,
        user: User,
        password: str,
        code: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        if not verify_password(password, user.passwordHash):
            raise UnauthorizedError(detail=INVALID_CREDENTIALS_MESSAGE)

        cred = await self._get_mfa_credential(user.id)
        if cred is None or not cred.enabled:
            raise NotFoundError(detail="MFA non actif")

        secret_plain = decrypt_secret(cred.secret)
        ok = verify_totp(secret_plain, code)
        if not ok:
            matched, updated = consume_recovery_code(
                list(cred.recoveryCodesHashed or []), code
            )
            if matched:
                cred.recoveryCodesHashed = updated
                ok = True

        if not ok:
            raise UnauthorizedError(detail="Code MFA invalide")

        cred.enabled = False
        cred.verifiedAt = None
        user.mfaEnabled = False
        await self.session.flush()

        await self._audit(
            event=AuthEvent.MFA_DISABLED,
            user_id=user.id,
            email=user.email,
            ip=ip_address,
            ua=user_agent,
            success=True,
        )

    # =========================================================================
    # /sessions, /sessions/{id}
    # =========================================================================
    async def list_sessions(self, user: User) -> list[SessionInfo]:
        now = datetime.now(UTC)
        stmt = (
            select(RefreshTokenSession)
            .where(
                RefreshTokenSession.userId == user.id,
                RefreshTokenSession.revokedAt.is_(None),
                RefreshTokenSession.expiresAt > now,
            )
            .order_by(desc(RefreshTokenSession.createdAt))
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [SessionInfo.model_validate(r) for r in rows]

    async def revoke_session(
        self,
        user: User,
        session_id: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        row = await self.session.get(RefreshTokenSession, session_id)
        if row is None or row.userId != user.id:
            raise NotFoundError(detail="Session introuvable")
        if row.revokedAt is not None:
            return
        row.revokedAt = datetime.now(UTC)
        row.revokedReason = "manual_revoke"
        await self.session.flush()
        await self._audit(
            event=AuthEvent.SESSION_REVOKED,
            user_id=user.id,
            email=user.email,
            ip=ip_address,
            ua=user_agent,
            success=True,
        )

    # =========================================================================
    # Internals
    # =========================================================================
    async def _load_user_by_email(self, email: str) -> User | None:
        stmt = (
            select(User)
            .where(User.email == email)
            .options(
                selectinload(User.region),
                selectinload(User.prefecture),
                selectinload(User.subPrefecture),
                selectinload(User.school),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _load_user_by_id(self, user_id: str) -> User | None:
        stmt = (
            select(User)
            .where(User.id == user_id)
            .options(
                selectinload(User.region),
                selectinload(User.prefecture),
                selectinload(User.subPrefecture),
                selectinload(User.school),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _get_mfa_credential(self, user_id: str) -> MfaCredential | None:
        stmt = select(MfaCredential).where(MfaCredential.userId == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _password_history(self, user_id: str, limit: int) -> list[PasswordHistory]:
        stmt = (
            select(PasswordHistory)
            .where(PasswordHistory.userId == user_id)
            .order_by(desc(PasswordHistory.createdAt))
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _find_session_by_hash(self, token_hash: str) -> RefreshTokenSession | None:
        stmt = select(RefreshTokenSession).where(
            RefreshTokenSession.tokenHash == token_hash
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _issue_session(
        self,
        user: User,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> LoginResponse:
        """Mint a new access+refresh pair AND persist the refresh session."""
        claims = {
            "role": user.role.value,
            "regionId": user.regionId,
            "prefectureId": user.prefectureId,
            "subPrefectureId": user.subPrefectureId,
            "schoolId": user.schoolId,
        }
        access = create_access_token(user.id, claims=claims)
        refresh = create_refresh_token(user.id, claims=claims)

        # Persist the refresh session (source of truth for revocation).
        # Module 1.1 — H-3 — sanitize the user-agent before INSERT: Postgres
        # rejects NUL bytes (0x00) in TEXT columns, and a malicious client
        # could otherwise crash /login by sending a header with embedded NULs.
        # We apply the same cap as the audit log for consistency.
        refresh_payload = decode_token(refresh, expected_type="refresh")
        expires_at = datetime.fromtimestamp(int(refresh_payload["exp"]), tz=UTC)
        safe_ua = _sanitize_audit_string(
            user_agent, max_length=_AUDIT_USER_AGENT_MAX
        )
        self.session.add(
            RefreshTokenSession(
                userId=user.id,
                tokenHash=hash_token(refresh),
                userAgent=safe_ua,
                ipAddress=ip_address,
                expiresAt=expires_at,
                lastUsedAt=datetime.now(UTC),
            )
        )
        await self.session.flush()

        # Reset login counters now that we authenticated for real.
        if self.redis is not None:
            await reset_login_counters(self.redis, user.email, ip_address or "")

        await self._audit(
            event=AuthEvent.LOGIN_SUCCESS,
            user_id=user.id,
            email=user.email,
            ip=ip_address,
            ua=user_agent,
            success=True,
        )
        return LoginResponse(
            accessToken=access,
            refreshToken=refresh,
            user=LoginUser.model_validate(user),
        )

    # =========================================================================
    # /audit-log — Module 1.1 H-2 — let users see their own auth events
    # =========================================================================
    async def list_audit_log(
        self,
        *,
        current_user: User,
        target_user_id: str | None = None,
        limit: int = 50,
    ) -> list[AuthAuditLog]:
        """Return audit rows the caller is allowed to read.

        Authorisation matrix:
            * Regular user -> can only read their own rows.
            * NATIONAL_ADMIN / MINISTRY_ADMIN -> can read any user's rows by
              passing ``target_user_id``. When omitted, defaults to their
              own rows (same as a regular user).
        """
        # Resolve the effective filter respecting RBAC. NEVER trust the caller:
        # a non-admin who passes target_user_id MUST still get only their own.
        if (
            target_user_id is not None
            and current_user.role in (UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN)
        ):
            filter_user_id = target_user_id
        else:
            filter_user_id = current_user.id

        # Limit clamped at the service layer too (defense in depth — router
        # also clamps via Query(le=200)).
        capped_limit = max(1, min(int(limit), 200))

        stmt = (
            select(AuthAuditLog)
            .where(AuthAuditLog.userId == filter_user_id)
            .order_by(desc(AuthAuditLog.createdAt))
            .limit(capped_limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows)

    async def _audit(
        self,
        *,
        event: str,
        user_id: str | None = None,
        email: str | None = None,
        ip: str | None = None,
        ua: str | None = None,
        success: bool = True,
        reason: str | None = None,
    ) -> None:
        # Module 1.1 — H-3 — every free-form column gets bounded length and
        # control characters stripped. Two attack vectors blocked at once:
        #   * DoS: a malicious client sending 1 MB User-Agent headers would
        #     bloat the AuthAuditLog table; truncate at 512 bytes.
        #   * Log injection: CR/LF/NUL/ESC bytes in UA / email would break
        #     downstream log shippers and SIEM parsers, or smuggle fake
        #     entries into Loguru's stdout sink.
        safe_ua = _sanitize_audit_string(ua, max_length=_AUDIT_USER_AGENT_MAX)
        safe_email = _sanitize_audit_string(email, max_length=_AUDIT_EMAIL_MAX)
        safe_reason = _sanitize_audit_string(reason, max_length=_AUDIT_REASON_MAX)
        try:
            self.session.add(
                AuthAuditLog(
                    userId=user_id,
                    email=safe_email,
                    event=event,
                    ipAddress=ip,
                    userAgent=safe_ua,
                    success=success,
                    failureReason=safe_reason,
                )
            )
            await self.session.flush()
        except Exception as exc:  # pragma: no cover - defensive
            # Auditing is never allowed to break a primary flow.
            logger.warning("auth audit insert failed: {}", exc)


# Tiny sanity import so ruff sees `settings` is used.
_ = settings
