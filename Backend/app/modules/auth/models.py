from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, CreatedAtMixin, TimestampMixin, cuid_pk
from app.shared.enums import UserRole

if TYPE_CHECKING:
    from app.modules.attendance.models import AttendanceRecord  # noqa: F401
    from app.modules.census.models import StudentTransfer  # noqa: F401
    from app.modules.schools.models import School  # noqa: F401
    from app.modules.territory.models import Prefecture, Region, SubPrefecture  # noqa: F401
    from app.modules.workflow.models import (  # noqa: F401
        AuditLog,
        Notification,
        ValidationRequest,
    )


class User(Base, TimestampMixin):
    __tablename__ = "User"

    id: Mapped[str] = cuid_pk()
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    passwordHash: Mapped[str] = mapped_column(String, nullable=False)
    fullName: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="UserRole", native_enum=True), nullable=False
    )

    regionId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Region.id"), nullable=True
    )
    prefectureId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Prefecture.id"), nullable=True
    )
    subPrefectureId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("SubPrefecture.id"), nullable=True
    )
    schoolId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=True
    )

    isActive: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- Module 1 hardening columns ---
    mfaRequired: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    mfaEnabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    passwordChangedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Module 6 i18n column ---
    # ISO 639-1/3 code among ['fr', 'ff', 'sus', 'man'] — fallback 'fr'.
    preferredLanguage: Mapped[str] = mapped_column(
        String(8), default="fr", server_default="fr", nullable=False
    )

    # --- Relationships ---
    region: Mapped["Region | None"] = relationship(back_populates="users", lazy="raise")
    prefecture: Mapped["Prefecture | None"] = relationship(back_populates="users", lazy="raise")
    subPrefecture: Mapped["SubPrefecture | None"] = relationship(
        back_populates="users", lazy="raise"
    )
    school: Mapped["School | None"] = relationship(back_populates="users", lazy="raise")

    auditLogs: Mapped[list["AuditLog"]] = relationship(
        back_populates="actor", lazy="raise", cascade="all, delete-orphan"
    )
    studentTransfers: Mapped[list["StudentTransfer"]] = relationship(
        back_populates="actor", lazy="raise"
    )
    requestedValidations: Mapped[list["ValidationRequest"]] = relationship(
        back_populates="requestedBy",
        foreign_keys="ValidationRequest.requestedById",
        lazy="raise",
    )
    reviewedValidations: Mapped[list["ValidationRequest"]] = relationship(
        back_populates="reviewer",
        foreign_keys="ValidationRequest.reviewerUserId",
        lazy="raise",
    )
    notificationsReceived: Mapped[list["Notification"]] = relationship(
        back_populates="recipient",
        foreign_keys="Notification.recipientUserId",
        lazy="raise",
    )
    notificationsSent: Mapped[list["Notification"]] = relationship(
        back_populates="sender",
        foreign_keys="Notification.senderUserId",
        lazy="raise",
    )


# ---------------------------------------------------------------------------
# Module 1 — Auth hardening tables
# ---------------------------------------------------------------------------
class MfaCredential(Base, TimestampMixin):
    """TOTP enrollment for a single user. One row per user (unique).

    `secret` is AES-256-GCM encrypted (see :func:`app.core.security.encrypt_secret`).
    `recoveryCodesHashed` stores the Argon2 hashes of single-use recovery codes.
    """
    __tablename__ = "MfaCredential"

    id: Mapped[str] = cuid_pk()
    userId: Mapped[str] = mapped_column(
        String(30), ForeignKey("User.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    secret: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    verifiedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recoveryCodesHashed: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )


class PasswordHistory(Base, CreatedAtMixin):
    """Append-only history of previous password hashes — used to block reuse."""
    __tablename__ = "PasswordHistory"
    __table_args__ = (
        Index("ix_PasswordHistory_userId_createdAt", "userId", "createdAt"),
    )

    id: Mapped[str] = cuid_pk()
    userId: Mapped[str] = mapped_column(
        String(30), ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )
    passwordHash: Mapped[str] = mapped_column(String, nullable=False)


class RefreshTokenSession(Base, CreatedAtMixin):
    """Active refresh-token sessions. One row per `/login` (or `/refresh` rotation).

    Revoking a session here is the source of truth — the Redis JTI blacklist
    is the in-memory cache that makes the check fast on the hot path.
    """
    __tablename__ = "RefreshTokenSession"
    __table_args__ = (
        Index(
            "ix_RefreshTokenSession_userId_createdAt", "userId", "createdAt"
        ),
    )

    id: Mapped[str] = cuid_pk()
    userId: Mapped[str] = mapped_column(
        String(30), ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )
    tokenHash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    userAgent: Mapped[str | None] = mapped_column(String, nullable=True)
    ipAddress: Mapped[str | None] = mapped_column(String, nullable=True)
    lastUsedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expiresAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revokedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revokedReason: Mapped[str | None] = mapped_column(String, nullable=True)


class AuthAuditLog(Base):
    """Append-only audit trail of every authentication-related event.

    Has its own `createdAt` (NOT TimestampMixin — no updatedAt).
    """
    __tablename__ = "AuthAuditLog"
    __table_args__ = (
        Index("ix_AuthAuditLog_userId_createdAt", "userId", "createdAt"),
        Index("ix_AuthAuditLog_email_createdAt", "email", "createdAt"),
    )

    id: Mapped[str] = cuid_pk()
    userId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id", ondelete="SET NULL"), nullable=True
    )
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    event: Mapped[str] = mapped_column(String, nullable=False)
    ipAddress: Mapped[str | None] = mapped_column(String, nullable=True)
    userAgent: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failureReason: Mapped[str | None] = mapped_column(String, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class PasswordResetToken(Base, CreatedAtMixin):
    """Single-use token for the password reset flow.

    `tokenHash` = SHA-256 of the bearer token; the bearer is sent by email
    and never persisted in clear.
    """
    __tablename__ = "PasswordResetToken"

    id: Mapped[str] = cuid_pk()
    userId: Mapped[str] = mapped_column(
        String(30), ForeignKey("User.id", ondelete="CASCADE"), nullable=False
    )
    tokenHash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    expiresAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    usedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ipAddress: Mapped[str | None] = mapped_column(String, nullable=True)


# Event constants used in AuthAuditLog.event (kept as module-level strings so
# the service layer doesn't import an enum just for one column).
class AuthEvent:
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    MFA_SUCCESS = "MFA_SUCCESS"
    MFA_FAILED = "MFA_FAILED"
    LOGOUT = "LOGOUT"
    REFRESH = "REFRESH"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    MFA_ENABLED = "MFA_ENABLED"
    MFA_DISABLED = "MFA_DISABLED"
    PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED"
    PASSWORD_RESET_USED = "PASSWORD_RESET_USED"
    RATE_LIMITED = "RATE_LIMITED"
    SESSION_REVOKED = "SESSION_REVOKED"
    # Module 1.1 — emitted when /mfa/setup creates a *pending* credential.
    # Prevents AuthEvent.MFA_ENABLED success=False from polluting "MFA enabled"
    # dashboards: the user did not finish enrolment yet, no need to fire a
    # failure alert.
    MFA_SETUP_INITIATED = "MFA_SETUP_INITIATED"
