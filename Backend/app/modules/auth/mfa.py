"""TOTP / recovery code primitives for the auth module.

Wraps `pyotp` so the service layer does not import the third-party SDK
directly — this gives us a single place to swap algorithms or add
WebAuthn later.
"""
from __future__ import annotations

import os

import pyotp

from app.core.config import settings
from app.core.security import (
    generate_recovery_codes as _generate_recovery_codes,
)
from app.core.security import (
    hash_recovery_code,
    verify_recovery_code,
)

MFA_ISSUER = os.getenv("MFA_ISSUER", "GESTION-EE")
MFA_DEFAULT_WINDOW = 1  # accept the previous & next 30 s windows
RECOVERY_CODES_COUNT = 10


def generate_secret() -> str:
    """Random 32-character base32 TOTP secret (160 bits — RFC 6238 recommended)."""
    return pyotp.random_base32()


def provisioning_uri(email: str, secret: str, *, issuer: str | None = None) -> str:
    """Build the otpauth:// URI consumed by Google Authenticator / 1Password / etc."""
    name = email.strip().lower()
    iss = issuer or MFA_ISSUER
    return pyotp.TOTP(secret).provisioning_uri(name=name, issuer_name=iss)


def verify_totp(secret: str, code: str, *, window: int = MFA_DEFAULT_WINDOW) -> bool:
    """Verify a 6-digit TOTP code with a `window` tolerance (±N * 30s)."""
    normalized = (code or "").strip().replace(" ", "")
    if not normalized.isdigit() or len(normalized) != 6:
        return False
    return pyotp.TOTP(secret).verify(normalized, valid_window=window)


def hash_recovery_codes(codes: list[str]) -> list[str]:
    """Argon2-hash each recovery code. Returned list keeps the original order."""
    return [hash_recovery_code(c) for c in codes]


def consume_recovery_code(
    hashed_codes: list[str], candidate: str
) -> tuple[bool, list[str]]:
    """Try to consume one recovery code.

    Returns `(matched, updated_hashed_codes)`. When `matched=True`, the
    consumed code is removed from the returned list — recovery codes are
    strictly single-use.
    """
    if not candidate or not candidate.strip():
        return False, hashed_codes
    for idx, hashed in enumerate(hashed_codes):
        if verify_recovery_code(hashed, candidate):
            updated = list(hashed_codes)
            updated.pop(idx)
            return True, updated
    return False, hashed_codes


def fresh_recovery_codes() -> tuple[list[str], list[str]]:
    """Return `(plain_codes, hashed_codes)` for a brand-new enrollment.

    `plain_codes` is shown to the user **once** — they must store them
    somewhere safe. `hashed_codes` is what we persist.
    """
    plain = _generate_recovery_codes(n=RECOVERY_CODES_COUNT)
    hashed = hash_recovery_codes(plain)
    return plain, hashed


# Re-export so `from app.modules.auth.mfa import ...` is one-stop.
__all__ = [
    "MFA_ISSUER",
    "RECOVERY_CODES_COUNT",
    "consume_recovery_code",
    "fresh_recovery_codes",
    "generate_secret",
    "hash_recovery_codes",
    "provisioning_uri",
    "verify_totp",
]


# Reference `settings` so the import is recognised as load-bearing — keeps
# the cycle `config -> security -> mfa` warm during app startup.
_ = settings
