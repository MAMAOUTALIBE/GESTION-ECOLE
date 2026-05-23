from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, TimestampMixin, cuid_pk
from app.shared.enums import LibraryLoanStatus, LibraryStockStatus

if TYPE_CHECKING:
    from app.modules.academics.models import Subject
    from app.modules.census.models import Student
    from app.modules.schools.models import School


class LibraryInventory(Base, TimestampMixin):
    __tablename__ = "LibraryInventory"
    __table_args__ = (
        UniqueConstraint(
            "schoolId",
            "subjectId",
            "level",
            "title",
            name="uq_LibraryInventory_schoolId_subjectId_level_title",
        ),
        Index("ix_LibraryInventory_schoolId_status", "schoolId", "status"),
        Index("ix_LibraryInventory_subjectId", "subjectId"),
        Index("ix_LibraryInventory_status", "status"),
    )

    id: Mapped[str] = cuid_pk()
    schoolId: Mapped[str] = mapped_column(String(30), ForeignKey("School.id"), nullable=False)
    subjectId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Subject.id"), nullable=False
    )
    level: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)
    damaged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    required: Mapped[int] = mapped_column(Integer, nullable=False)
    lastInventoryAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[LibraryStockStatus] = mapped_column(
        Enum(LibraryStockStatus, name="LibraryStockStatus", native_enum=True),
        default=LibraryStockStatus.SUFFICIENT,
        nullable=False,
    )

    school: Mapped["School"] = relationship(back_populates="libraryInventory", lazy="raise")
    subject: Mapped["Subject"] = relationship(
        back_populates="libraryInventory", lazy="raise"
    )
    loans: Mapped[list["LibraryLoan"]] = relationship(
        back_populates="inventory", lazy="raise"
    )


class LibraryLoan(Base, TimestampMixin):
    __tablename__ = "LibraryLoan"
    __table_args__ = (
        Index("ix_LibraryLoan_inventoryId_status", "inventoryId", "status"),
        Index("ix_LibraryLoan_studentId_status", "studentId", "status"),
        Index("ix_LibraryLoan_dueAt_status", "dueAt", "status"),
    )

    id: Mapped[str] = cuid_pk()
    inventoryId: Mapped[str] = mapped_column(
        String(30), ForeignKey("LibraryInventory.id"), nullable=False
    )
    studentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=False
    )
    borrowedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dueAt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    returnedAt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[LibraryLoanStatus] = mapped_column(
        Enum(LibraryLoanStatus, name="LibraryLoanStatus", native_enum=True),
        default=LibraryLoanStatus.BORROWED,
        nullable=False,
    )

    inventory: Mapped["LibraryInventory"] = relationship(
        back_populates="loans", lazy="raise"
    )
    student: Mapped["Student"] = relationship(back_populates="libraryLoans", lazy="raise")
