from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, TimestampMixin, cuid_pk
from app.shared.enums import ValidationStatus

if TYPE_CHECKING:
    from app.modules.auth.models import User
    from app.modules.schools.models import School


class Region(Base, TimestampMixin):
    __tablename__ = "Region"

    id: Mapped[str] = cuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    prefectures: Mapped[list["Prefecture"]] = relationship(
        back_populates="region", lazy="raise"
    )
    schools: Mapped[list["School"]] = relationship(back_populates="region", lazy="raise")
    users: Mapped[list["User"]] = relationship(back_populates="region", lazy="raise")


class Prefecture(Base, TimestampMixin):
    __tablename__ = "Prefecture"
    __table_args__ = (Index("ix_Prefecture_regionId_status", "regionId", "status"),)

    id: Mapped[str] = cuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    regionId: Mapped[str] = mapped_column(String(30), ForeignKey("Region.id"), nullable=False)

    status: Mapped[ValidationStatus] = mapped_column(
        Enum(ValidationStatus, name="ValidationStatus", native_enum=True),
        default=ValidationStatus.APPROVED,
        nullable=False,
    )
    rejectionReason: Mapped[str | None] = mapped_column(String, nullable=True)
    createdById: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approvedById: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approvedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    region: Mapped["Region"] = relationship(back_populates="prefectures", lazy="raise")
    subPrefectures: Mapped[list["SubPrefecture"]] = relationship(
        back_populates="prefecture", lazy="raise"
    )
    users: Mapped[list["User"]] = relationship(back_populates="prefecture", lazy="raise")
    schools: Mapped[list["School"]] = relationship(
        back_populates="prefectureRef",
        foreign_keys="School.prefectureId",
        lazy="raise",
    )


class SubPrefecture(Base, TimestampMixin):
    __tablename__ = "SubPrefecture"
    __table_args__ = (
        Index("ix_SubPrefecture_regionId_status", "regionId", "status"),
        Index("ix_SubPrefecture_prefectureId_status", "prefectureId", "status"),
    )

    id: Mapped[str] = cuid_pk()
    name: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    regionId: Mapped[str] = mapped_column(String(30), nullable=False)
    prefectureId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Prefecture.id"), nullable=False
    )

    status: Mapped[ValidationStatus] = mapped_column(
        Enum(ValidationStatus, name="ValidationStatus", native_enum=True),
        default=ValidationStatus.APPROVED,
        nullable=False,
    )
    rejectionReason: Mapped[str | None] = mapped_column(String, nullable=True)
    createdById: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approvedById: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approvedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    prefecture: Mapped["Prefecture"] = relationship(
        back_populates="subPrefectures", lazy="raise"
    )
    schools: Mapped[list["School"]] = relationship(
        back_populates="subPrefecture", lazy="raise"
    )
    users: Mapped[list["User"]] = relationship(back_populates="subPrefecture", lazy="raise")
