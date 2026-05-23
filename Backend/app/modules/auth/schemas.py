"""Pydantic schemas for the auth module.

Contracts MUST stay byte-compatible with the NestJS responses consumed by the
Angular frontend (see Final/src/app/shared/services/auth.service.ts).
"""
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
    """POST /api/auth/login response."""
    accessToken: str
    user: LoginUser


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
