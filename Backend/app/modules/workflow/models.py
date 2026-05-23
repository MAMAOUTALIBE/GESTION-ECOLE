from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, CreatedAtMixin, TimestampMixin, cuid_pk
from app.shared.enums import (
    NotificationType,
    UserRole,
    ValidationEntityType,
    ValidationStatus,
)

if TYPE_CHECKING:
    from app.modules.auth.models import User


class ValidationRequest(Base, TimestampMixin):
    __tablename__ = "ValidationRequest"
    __table_args__ = (
        Index("ix_ValidationRequest_entityType_entityId", "entityType", "entityId"),
        Index("ix_ValidationRequest_status_reviewerRole", "status", "reviewerRole"),
        Index(
            "ix_ValidationRequest_requestedById_createdAt", "requestedById", "createdAt"
        ),
    )

    id: Mapped[str] = cuid_pk()
    entityType: Mapped[ValidationEntityType] = mapped_column(
        Enum(ValidationEntityType, name="ValidationEntityType", native_enum=True),
        nullable=False,
    )
    entityId: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[ValidationStatus] = mapped_column(
        Enum(ValidationStatus, name="ValidationStatus", native_enum=True),
        default=ValidationStatus.SUBMITTED,
        nullable=False,
    )
    requestedById: Mapped[str] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=False
    )
    reviewerRole: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="UserRole", native_enum=True), nullable=False
    )
    reviewerRegionId: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reviewerPrefectureId: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reviewerSubPrefectureId: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reviewerUserId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requestedBy: Mapped["User"] = relationship(
        back_populates="requestedValidations",
        foreign_keys=[requestedById],
        lazy="raise",
    )
    reviewer: Mapped["User | None"] = relationship(
        back_populates="reviewedValidations",
        foreign_keys=[reviewerUserId],
        lazy="raise",
    )


class Notification(Base, CreatedAtMixin):
    __tablename__ = "Notification"
    __table_args__ = (
        Index(
            "ix_Notification_recipientUserId_isRead_createdAt",
            "recipientUserId",
            "isRead",
            "createdAt",
        ),
        Index("ix_Notification_entityType_entityId", "entityType", "entityId"),
    )

    id: Mapped[str] = cuid_pk()
    recipientUserId: Mapped[str] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=False
    )
    senderUserId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="NotificationType", native_enum=True), nullable=False
    )
    entityType: Mapped[ValidationEntityType | None] = mapped_column(
        Enum(ValidationEntityType, name="ValidationEntityType", native_enum=True),
        nullable=True,
    )
    entityId: Mapped[str | None] = mapped_column(String(30), nullable=True)
    isRead: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    readAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    recipient: Mapped["User"] = relationship(
        back_populates="notificationsReceived",
        foreign_keys=[recipientUserId],
        lazy="raise",
    )
    sender: Mapped["User | None"] = relationship(
        back_populates="notificationsSent",
        foreign_keys=[senderUserId],
        lazy="raise",
    )


class AuditLog(Base, CreatedAtMixin):
    __tablename__ = "AuditLog"

    id: Mapped[str] = cuid_pk()
    actorId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    entity: Mapped[str] = mapped_column(String, nullable=False)
    entityId: Mapped[str | None] = mapped_column(String(30), nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )

    actor: Mapped["User | None"] = relationship(back_populates="auditLogs", lazy="raise")
