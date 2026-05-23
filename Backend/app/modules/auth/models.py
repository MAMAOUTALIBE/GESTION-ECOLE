from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, TimestampMixin, cuid_pk
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
