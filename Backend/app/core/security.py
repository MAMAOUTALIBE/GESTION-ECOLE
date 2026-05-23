from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from passlib.hash import bcrypt

from app.core.config import settings

_argon2 = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

TokenType = Literal["access", "refresh"]


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


def _create_token(
    subject: str,
    token_type: TokenType,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    if token_type == "access":
        exp = now + timedelta(minutes=settings.jwt_access_token_ttl_minutes)
    else:
        exp = now + timedelta(days=settings.jwt_refresh_token_ttl_days)

    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str, claims: dict[str, Any] | None = None) -> str:
    return _create_token(user_id, "access", claims)


def create_refresh_token(user_id: str, claims: dict[str, Any] | None = None) -> str:
    return _create_token(user_id, "refresh", claims)


def decode_token(token: str, *, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Decode and validate a JWT. Raises jwt.* exceptions on failure."""
    payload: dict[str, Any] = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
    if expected_type and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"Expected token type {expected_type}")
    return payload
