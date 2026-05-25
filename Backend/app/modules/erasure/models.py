"""Module 5D — Modèle SQLAlchemy de la demande de droit à l'oubli."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.erasure.enums import ErasureReason, ErasureStatus
from app.shared.base import Base, TimestampMixin, cuid_pk

if TYPE_CHECKING:
    from app.modules.auth.models import User
    from app.modules.census.models import Student


class ErasureRequest(Base, TimestampMixin):
    """Demande de droit à l'oubli pour un élève.

    États successifs (voir ``ErasureStatus`` pour la spec complète) :
    ``GRACE_PERIOD`` → ``EXECUTED`` (ou ``CANCELLED`` si annulé pendant
    la fenêtre). Le défaut DB est ``PENDING`` (filet de sécurité ; la
    couche service force ``GRACE_PERIOD`` à la création).

    Note : ``studentId`` est ``ON DELETE SET NULL`` — après EXECUTED le
    student peut être supprimé physiquement à terme (politique de
    rétention par école), mais la trace de la demande reste pour les
    audits CNIL/MENA.
    """

    __tablename__ = "ErasureRequest"
    __table_args__ = (
        Index(
            "ix_ErasureRequest_status_gracePeriodUntil",
            "status",
            "gracePeriodUntil",
        ),
        Index("ix_ErasureRequest_studentId", "studentId"),
        Index(
            "ix_ErasureRequest_requestedAt",
            "requestedAt",
        ),
    )

    id: Mapped[str] = cuid_pk()
    studentId: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("Student.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[ErasureReason] = mapped_column(
        Enum(ErasureReason, name="ErasureReason", native_enum=True),
        nullable=False,
    )
    reasonDetails: Mapped[str | None] = mapped_column(Text, nullable=True)
    requestedById: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("User.id", ondelete="SET NULL"),
        nullable=True,
    )
    requestedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    status: Mapped[ErasureStatus] = mapped_column(
        Enum(ErasureStatus, name="ErasureStatus", native_enum=True),
        default=ErasureStatus.PENDING,
        server_default="PENDING",
        nullable=False,
    )
    gracePeriodUntil: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    executedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    executedById: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("User.id", ondelete="SET NULL"),
        nullable=True,
    )
    cancelledById: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("User.id", ondelete="SET NULL"),
        nullable=True,
    )
    cancelledAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancellationReason: Mapped[str | None] = mapped_column(Text, nullable=True)

    student: Mapped["Student | None"] = relationship(
        foreign_keys=[studentId], lazy="raise"
    )
    requestedBy: Mapped["User | None"] = relationship(
        foreign_keys=[requestedById], lazy="raise"
    )
    executedBy: Mapped["User | None"] = relationship(
        foreign_keys=[executedById], lazy="raise"
    )
    cancelledBy: Mapped["User | None"] = relationship(
        foreign_keys=[cancelledById], lazy="raise"
    )


__all__ = ["ErasureRequest"]
