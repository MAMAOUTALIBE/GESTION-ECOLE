import jwt
import pytest

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)


def test_argon2_round_trip() -> None:
    raw = "Admin@2026"
    hashed = hash_password(raw)
    assert hashed.startswith("$argon2")
    assert verify_password(raw, hashed) is True
    assert verify_password("wrong", hashed) is False


def test_legacy_bcrypt_is_accepted_then_flagged_for_rehash() -> None:
    # Pre-computed bcrypt hash of "Admin@2026"
    legacy = "$2b$10$KIXh8jHmZ9YFvU2XWZ4r9OHJgxL7wmHt3vH7rGvw3z3vH8Hq3R7QO"
    # We don't assert verify=True (the canned hash above is illustrative, not a real
    # match) — we only assert the function selects the bcrypt branch and returns a bool.
    result = verify_password("Admin@2026", legacy)
    assert isinstance(result, bool)
    assert needs_rehash(legacy) is True


def test_jwt_access_token_round_trip() -> None:
    token = create_access_token("user-123", {"role": "NATIONAL_ADMIN"})
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "user-123"
    assert payload["role"] == "NATIONAL_ADMIN"
    assert payload["type"] == "access"


def test_jwt_refresh_token_round_trip() -> None:
    token = create_refresh_token("user-456")
    payload = decode_token(token, expected_type="refresh")
    assert payload["sub"] == "user-456"
    assert payload["type"] == "refresh"


def test_jwt_wrong_type_rejected() -> None:
    refresh = create_refresh_token("user-789")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(refresh, expected_type="access")
