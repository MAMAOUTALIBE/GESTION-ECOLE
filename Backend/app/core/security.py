"""Crypto primitives — password hashing, JWT minting/decoding, MFA secret
encryption, recovery codes, and Redis-backed JTI revocation.

Module 1 hardening notes
------------------------
* Every JWT now carries a `jti` (UUID hex) so it can be revoked individually
  via :func:`revoke_token` (Redis blacklist with TTL aligned on token exp).
* `decode_token` stays synchronous and pure (validates signature + type only).
  Revocation check lives in :func:`is_token_revoked` and is called from the
  FastAPI dependency `get_current_user` (and from the auth service for refresh).
* MFA TOTP secrets are encrypted at rest with AES-256-GCM. The key is derived
  from `JWT_SECRET` via HKDF-SHA256 with `info="gestionee.mfa.v1"`. Rotating
  `JWT_SECRET` invalidates all stored TOTP secrets — operators must re-enroll.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from passlib.hash import bcrypt
from redis.asyncio import Redis

from app.core.config import settings

# Module 1 security fix C-2 — hard pin the JWT algorithms accepted by
# `decode_token`. NEVER rely on `settings.jwt_algorithm` directly when
# decoding: an attacker who controls the env (or a misconfigured deploy)
# could swap it to "none" or to "RS256" and trigger alg-confusion attacks.
# The pin is checked once on every decode AND used as the `algorithms=`
# allow-list passed to `jwt.decode`.
_ALLOWED_JWT_ALGORITHMS: Final[frozenset[str]] = frozenset({"HS256"})

_argon2 = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

TokenType = Literal["access", "refresh", "mfa_challenge"]

# Redis key prefix for revoked JTIs. Value is "1", TTL = remaining token life.
REVOKED_JTI_PREFIX = "auth:revoked:"
# HKDF info string — version it so we can rotate the derivation scheme later.
_MFA_KEY_INFO = b"gestionee.mfa.v1"
_MFA_KEY_LEN = 32  # AES-256


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    """Hash with Argon2id (modern default)."""
    return _argon2.hash(plain)


def verify_password(plain: str, stored_hash: str) -> bool:
    """Verify Argon2 hash; falls back to bcrypt for legacy NestJS users.

    Returns True on match. The caller should re-hash with Argon2 when a
    bcrypt-hashed password matches, to migrate the user to the new scheme.
    """
    if stored_hash.startswith("$argon2"):
        try:
            _argon2.verify(stored_hash, plain)
            return True
        except VerifyMismatchError:
            return False
    if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.verify(plain, stored_hash)
        except (ValueError, TypeError):
            return False
    return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the hash is bcrypt or Argon2 needs an upgrade."""
    if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
        return True
    if stored_hash.startswith("$argon2"):
        return _argon2.check_needs_rehash(stored_hash)
    return True


# ---------------------------------------------------------------------------
# JWT minting & decoding
# ---------------------------------------------------------------------------
def _create_token(
    subject: str,
    token_type: TokenType,
    extra_claims: dict[str, Any] | None = None,
    *,
    ttl: timedelta | None = None,
) -> str:
    now = datetime.now(UTC)
    if ttl is not None:
        exp = now + ttl
    elif token_type == "access":
        exp = now + timedelta(minutes=settings.jwt_access_token_ttl_minutes)
    elif token_type == "refresh":
        exp = now + timedelta(days=settings.jwt_refresh_token_ttl_days)
    else:
        # mfa_challenge — short-lived (5 min) by default
        exp = now + timedelta(minutes=5)

    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str, claims: dict[str, Any] | None = None) -> str:
    return _create_token(user_id, "access", claims)


def create_refresh_token(user_id: str, claims: dict[str, Any] | None = None) -> str:
    return _create_token(user_id, "refresh", claims)


def create_mfa_challenge_token(user_id: str, ttl_minutes: int = 5) -> str:
    """Short-lived token returned by /login when MFA is required.

    Carries `type=mfa_challenge` so it cannot be mistakenly accepted by
    `get_current_user` (which demands type=access).
    """
    return _create_token(
        user_id,
        "mfa_challenge",
        ttl=timedelta(minutes=ttl_minutes),
    )


def decode_token(token: str, *, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Decode and validate a JWT signature/expiry. Pure — no Redis call.

    Raises jwt.* exceptions on failure. Revocation check is a separate
    coroutine (:func:`is_token_revoked`) so callers can decide whether the
    blacklist is in scope (e.g. tests for token shape only do not need it).

    Security fix C-2 — the `algorithms=` allow-list is hard-pinned to
    `_ALLOWED_JWT_ALGORITHMS`, never the value of `settings.jwt_algorithm`.
    A misconfigured deploy that sets `JWT_ALGORITHM=none` or `RS256` is
    rejected at startup of every decode with a `RuntimeError`, well before
    the attacker payload reaches `jwt.decode`.
    """
    if settings.jwt_algorithm not in _ALLOWED_JWT_ALGORITHMS:
        raise RuntimeError(
            f"JWT_ALGORITHM forbidden: {settings.jwt_algorithm!r} "
            f"(allowed: {sorted(_ALLOWED_JWT_ALGORITHMS)})"
        )

    payload: dict[str, Any] = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=list(_ALLOWED_JWT_ALGORITHMS),
    )
    if expected_type and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"Expected token type {expected_type}")
    return payload


# ---------------------------------------------------------------------------
# JTI revocation (Redis blacklist)
# ---------------------------------------------------------------------------
def hash_token(token: str) -> str:
    """SHA-256 hex digest — used to store opaque references to tokens in DB
    without persisting the bearer value (RefreshTokenSession.tokenHash and
    PasswordResetToken.tokenHash both store this).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def revoke_token(redis: Redis, jti: str, exp: int) -> None:
    """Add a JTI to the Redis blacklist.

    The TTL equals the remaining token life (`exp - now`, clamped to >= 1s)
    so revoked entries are GC'd automatically.
    """
    now_ts = int(datetime.now(UTC).timestamp())
    ttl = max(1, exp - now_ts)
    await redis.set(f"{REVOKED_JTI_PREFIX}{jti}", "1", ex=ttl)


async def is_token_revoked(redis: Redis, jti: str) -> bool:
    """True if the JTI is in the Redis blacklist."""
    return bool(await redis.exists(f"{REVOKED_JTI_PREFIX}{jti}"))


# ---------------------------------------------------------------------------
# MFA secret encryption (AES-256-GCM with HKDF-derived key)
# ---------------------------------------------------------------------------
def _derive_mfa_key() -> bytes:
    """Derive a deterministic 32-byte key from JWT_SECRET via HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_MFA_KEY_LEN,
        salt=None,
        info=_MFA_KEY_INFO,
    )
    return hkdf.derive(settings.jwt_secret.encode("utf-8"))


def encrypt_secret(plain: str) -> str:
    """Encrypt a TOTP secret with AES-256-GCM.

    Returns a base64-encoded blob of `nonce(12) || ciphertext+tag`.
    """
    key = _derive_mfa_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plain.encode("utf-8"), associated_data=None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Reverse of :func:`encrypt_secret`. Raises on tamper / wrong key."""
    blob = base64.b64decode(ciphertext.encode("ascii"))
    nonce, ct = blob[:12], blob[12:]
    aesgcm = AESGCM(_derive_mfa_key())
    return aesgcm.decrypt(nonce, ct, associated_data=None).decode("utf-8")


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------
# Security fix C-3 — previous implementation used `secrets.choice` over a
# 36-char alphabet for 8 chars, yielding ~41 bits of entropy. We now use
# `secrets.token_urlsafe(20)` which gives 160 bits, then format the result
# in fixed-size groups for readability. Hashes / verification are unchanged
# (both still go through Argon2id on the normalised string).
_RECOVERY_RANDOM_BYTES: Final[int] = 20  # -> 160 bits of entropy
_RECOVERY_GROUP_SIZE: Final[int] = 4


def _format_recovery_code(raw: str) -> str:
    """Insert dashes every `_RECOVERY_GROUP_SIZE` chars (e.g. ABCD-EFGH-...)."""
    upper = raw.upper()
    return "-".join(
        upper[i : i + _RECOVERY_GROUP_SIZE]
        for i in range(0, len(upper), _RECOVERY_GROUP_SIZE)
    )


def generate_recovery_codes(n: int = 10) -> list[str]:
    """Generate `n` cryptographically strong recovery codes.

    Each code carries ~160 bits of entropy (token_urlsafe(20) → 27 chars of
    a 64-char alphabet), then is formatted in dash-separated groups of 4
    for human transcription. We strip URL-safe characters that are easy to
    confuse with each other (``-`` and ``_``) BEFORE inserting the
    dash separators — final shape is purely [A-Z0-9-].
    """
    codes: list[str] = []
    for _ in range(n):
        raw = secrets.token_urlsafe(_RECOVERY_RANDOM_BYTES)
        # token_urlsafe returns base64url alphabet (A-Z, a-z, 0-9, -, _).
        # Drop the separator-confusable characters and force upper-case so
        # the visible alphabet stays in [A-Z0-9] (~6 bits/char x 27 = 162b).
        cleaned = raw.replace("-", "").replace("_", "").upper()
        codes.append(_format_recovery_code(cleaned))
    return codes


def hash_recovery_code(code: str) -> str:
    """Hash a recovery code with Argon2id — exactly like a password."""
    return _argon2.hash(_normalize_recovery_code(code))


def verify_recovery_code(hashed: str, code: str) -> bool:
    """Verify a single recovery code candidate against its Argon2 hash."""
    try:
        _argon2.verify(hashed, _normalize_recovery_code(code))
        return True
    except VerifyMismatchError:
        return False


def _normalize_recovery_code(code: str) -> str:
    """Canonicalise a recovery code candidate for hashing/comparison.

    Strips whitespace, drops every dash (users sometimes omit them when
    typing) and upper-cases. Hash and verify MUST agree on this so that
    `verify_recovery_code(hash_recovery_code(c), c)` is always True.
    """
    return (code or "").strip().upper().replace("-", "").replace(" ", "")
