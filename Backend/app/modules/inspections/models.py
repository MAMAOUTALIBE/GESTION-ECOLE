"""Inspections — visites terrain par les inspecteurs avec rubrique standardisée.

Modèle :
    Inspection 1 → N InspectionFinding (constats par critère, avec sévérité)
    Inspection 1 → N InspectionActionItem (plan d'action de levée)

L'agrégation des `findings` produit un score 0-100 par inspection ; la
moyenne mobile par école est utilisée par Analytics pour le pilotage qualité.
"""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, CreatedAtMixin, TimestampMixin, cuid_pk
from app.shared.enums import (
    ActionItemStatus,
    FindingSeverity,
    InspectionCriterion,
    InspectionStatus,
)

if TYPE_CHECKING:
    from app.modules.auth.models import User
    from app.modules.schools.models import School


class Inspection(Base, TimestampMixin):
    __tablename__ = "Inspection"
    __table_args__ = (
        Index("ix_Inspection_schoolId_status", "schoolId", "status"),
        Index("ix_Inspection_inspectorId_scheduledDate", "inspectorId", "scheduledDate"),
        Index("ix_Inspection_status_performedDate", "status", "performedDate"),
    )

    id: Mapped[str] = cuid_pk()
    schoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False
    )
    inspectorId: Mapped[str] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=False
    )
    scheduledDate: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    performedDate: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[InspectionStatus] = mapped_column(
        Enum(InspectionStatus, name="InspectionStatus", native_enum=True),
        default=InspectionStatus.PLANNED,
        nullable=False,
    )
    # Score global 0-100, calculé à la complétion à partir des findings
    overallScore: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    school: Mapped["School"] = relationship(
        back_populates="inspections", lazy="raise"
    )
    inspector: Mapped["User"] = relationship(lazy="raise")
    findings: Mapped[list["InspectionFinding"]] = relationship(
        back_populates="inspection", lazy="raise", cascade="all, delete-orphan"
    )
    actionItems: Mapped[list["InspectionActionItem"]] = relationship(
        back_populates="inspection", lazy="raise", cascade="all, delete-orphan"
    )


class InspectionFinding(Base, CreatedAtMixin):
    """Un constat sur un critère donné, avec score 0-5 et sévérité."""
    __tablename__ = "InspectionFinding"
    __table_args__ = (
        Index("ix_InspectionFinding_inspectionId", "inspectionId"),
        Index("ix_InspectionFinding_criterion_severity", "criterion", "severity"),
    )

    id: Mapped[str] = cuid_pk()
    inspectionId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Inspection.id"), nullable=False
    )
    criterion: Mapped[InspectionCriterion] = mapped_column(
        Enum(InspectionCriterion, name="InspectionCriterion", native_enum=True),
        nullable=False,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)  # 0..5
    severity: Mapped[FindingSeverity] = mapped_column(
        Enum(FindingSeverity, name="FindingSeverity", native_enum=True),
        default=FindingSeverity.INFO,
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(String, nullable=True)
    photoUrl: Mapped[str | None] = mapped_column(String, nullable=True)

    inspection: Mapped["Inspection"] = relationship(
        back_populates="findings", lazy="raise"
    )


class InspectionActionItem(Base, TimestampMixin):
    """Action de levée associée à une inspection (avec date d'échéance)."""
    __tablename__ = "InspectionActionItem"
    __table_args__ = (
        Index("ix_InspectionActionItem_inspectionId_status", "inspectionId", "status"),
        Index("ix_InspectionActionItem_dueDate_status", "dueDate", "status"),
    )

    id: Mapped[str] = cuid_pk()
    inspectionId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Inspection.id"), nullable=False
    )
    description: Mapped[str] = mapped_column(String, nullable=False)
    dueDate: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ActionItemStatus] = mapped_column(
        Enum(ActionItemStatus, name="ActionItemStatus", native_enum=True),
        default=ActionItemStatus.OPEN,
        nullable=False,
    )
    resolvedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolvedById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )

    inspection: Mapped["Inspection"] = relationship(
        back_populates="actionItems", lazy="raise"
    )
    resolvedBy: Mapped["User | None"] = relationship(
        foreign_keys=[resolvedById], lazy="raise"
    )
