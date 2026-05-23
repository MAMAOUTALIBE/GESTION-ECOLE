"""Pydantic schemas for the auth module.

Contracts MUST stay byte-compatible with the NestJS responses consumed by the
Angular frontend (see Final/src/app/shared/services/auth.service.ts).

Module 1 hardening
------------------
* `LoginResponse.accessToken` is now `str | None` and `refreshToken` is
  `str | None`. The pre-MFA payload (no MFA enrolled) still serialises an
  `accessToken` string — angular keeps working unchanged. When MFA is
  enrolled, `accessToken`/`refreshToken` are `null` and `mfaChallenge`
  holds the short-lived JWT to POST to `/mfa/verify`.
* `MeUser` gained `mfaRequired` and `mfaEnabled` flags (additive — Angular
  ignores unknown fields).
* New request/response models for MFA, refresh, logout, password change,
  forgot/reset password, and session listing.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.shared.enums import UserRole


# --- Requests ---
class LoginRequest(BaseModel):
    """POST /api/auth/login body."""
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=8)


# --- Embedded territorial summary (used in login response) ---
class TerritorialEntitySummary(BaseModel):
    """Minimal { id, name, code } payload for region/prefecture/sub/school."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str


# --- /login response shape ---
class LoginUser(BaseModel):
    """Nested user object inside POST /api/auth/login response."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    fullName: str
    role: UserRole
    region: TerritorialEntitySummary | None = None
    prefecture: TerritorialEntitySummary | None = None
    subPrefecture: TerritorialEntitySummary | None = None
    school: TerritorialEntitySummary | None = None


class LoginResponse(BaseModel):
    """POST /api/auth/login response.

    Module 1 — additive fields only:
    * `refreshToken` (new, optional) issued alongside the access token.
    * `mfaChallenge` (new, optional) returned when the user has MFA enabled
      and must POST it to `/api/auth/mfa/verify` to receive real tokens.

    `accessToken` is `str | None` because we omit it during the MFA step;
    the pre-MFA frontend still receives a populated string and keeps
    working byte-compatibly.
    """
    accessToken: str | None = None
    refreshToken: str | None = None
    user: LoginUser | None = None
    mfaChallenge: str | None = None


# --- /me response shape (no nested objects, only IDs) ---
class MeUser(BaseModel):
    """Nested user object inside GET /api/auth/me response."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    fullName: str
    role: UserRole
    regionId: str | None = None
    prefectureId: str | None = None
    subPrefectureId: str | None = None
    schoolId: str | None = None
    # Module 1 — additive flags so the frontend can render an MFA banner.
    mfaRequired: bool = False
    mfaEnabled: bool = False


class MeResponse(BaseModel):
    """GET /api/auth/me response."""
    user: MeUser


# --- /users response shape ---
class UserListItem(BaseModel):
    """User row in the admin directory."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    fullName: str
    role: UserRole
    isActive: bool
    regionId: str | None = None
    prefectureId: str | None = None
    subPrefectureId: str | None = None
    schoolId: str | None = None
    region: TerritorialEntitySummary | None = None
    prefecture: TerritorialEntitySummary | None = None
    subPrefecture: TerritorialEntitySummary | None = None
    school: TerritorialEntitySummary | None = None
    createdAt: str
    updatedAt: str


# ---------------------------------------------------------------------------
# Module 1 — MFA / refresh / logout / password change / forgot-reset / sessions
# ---------------------------------------------------------------------------
class MfaVerifyRequest(BaseModel):
    """POST /api/auth/mfa/verify — finishes the login when MFA is on.

    `code` accepts either a 6-digit TOTP or a recovery code. Module 1.0
    recovery codes were 8 chars; the C-3 security fix moved them to a
    dashed 33-char format (`XXXX-XXXX-...`), hence `max_length=64` here.
    """
    challengeToken: str
    code: str = Field(min_length=6, max_length=64)


class MfaSetupRequest(BaseModel):
    """POST /api/auth/mfa/setup body.

    Security fix C-1 — the endpoint used to be parameterless. Anyone with
    a valid access token (e.g. stolen via XSS or replay) could call it and
    overwrite the victim's MFA credential silently, effectively neutralising
    MFA. We now demand:

    * ``currentPassword`` — always required; re-verified server-side.
    * ``currentTotp`` — only required when the user has ``mfaEnabled=True``
      already (re-enrollment / "lost my device" flow). Must be a valid TOTP
      or recovery code of the existing credential.
    """
    currentPassword: str = Field(min_length=8)
    currentTotp: str | None = Field(default=None, min_length=6, max_length=64)


class MfaSetupResponse(BaseModel):
    """POST /api/auth/mfa/setup response.

    `recoveryCodes` is returned ONCE, in clear. We never store the plain
    text — only the Argon2 hashes.
    """
    secret: str
    qrCodeUri: str
    recoveryCodes: list[str]


class MfaVerifySetupRequest(BaseModel):
    """POST /api/auth/mfa/verify-setup — activates a freshly enrolled credential."""
    code: str = Field(min_length=6, max_length=8)


class MfaDisableRequest(BaseModel):
    """POST /api/auth/mfa/disable — requires password + a TOTP/recovery code.

    `code` accepts either a 6-digit TOTP or a recovery code (33 chars after
    fix C-3 — see :class:`MfaVerifyRequest`).
    """
    password: str
    code: str = Field(min_length=6, max_length=64)


class RefreshRequest(BaseModel):
    refreshToken: str


class LogoutRequest(BaseModel):
    """POST /api/auth/logout — both fields optional so the client can choose
    to wipe only what it still has."""
    refreshToken: str | None = None


class ChangePasswordRequest(BaseModel):
    currentPassword: str = Field(min_length=8)
    newPassword: str = Field(min_length=12)
    confirmPassword: str = Field(min_length=12)


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    newPassword: str = Field(min_length=12)
    confirmPassword: str = Field(min_length=12)


class SessionInfo(BaseModel):
    """Single active refresh-token session (returned by GET /sessions)."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    userAgent: str | None = None
    ipAddress: str | None = None
    createdAt: datetime
    lastUsedAt: datetime | None = None
    expiresAt: datetime
