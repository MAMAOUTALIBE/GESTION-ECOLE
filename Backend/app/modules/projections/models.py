"""Module 2A — Modèle SQLAlchemy TransitionRate.

Une row ``TransitionRate`` par cellule
``(scope, entityId, schoolYearFromId, classLevelFrom, gender)``.

Pourquoi persister (pas live) ?
-------------------------------
* Le calcul scanne ``Enrollment`` filtré sur 2 années × N régions × M
  niveaux × 2 genres — cher en runtime si on l'appelle pour chaque hit
  dashboard.
* Les sources ``Enrollment`` peuvent être amendées rétroactivement
  (correction recensement). Un snapshot point-in-time stocké préserve la
  reproductibilité des rapports IIPE.
* Module 2B (projection cohorte) ré-utilise ces rates directement —
  pas besoin de re-calculer à chaque projection.

Index & unique
--------------
* ``(scope, entityId, schoolYearFromId)`` : "tous les rates d'une région
  pour une année donnée" — vue dashboard équité.
* ``(classLevelFrom, classLevelTo)`` : tri/filtre par paire de niveaux.
* Unique ``(scope, entityId, schoolYearFromId, classLevelFrom, gender)`` :
  garantit l'upsert idempotent au recalcul.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.enrollment.enums import EnrollmentClassLevel
from app.modules.projections.enums import TransitionScope
from app.shared.base import Base, CreatedAtMixin, cuid_pk
from app.shared.enums import Gender

if TYPE_CHECKING:
    from app.modules.academics.models import SchoolYear
    from app.modules.auth.models import User


class TransitionRate(Base, CreatedAtMixin):
    """Taux de transition d'un niveau N vers N+1, désagrégé par genre.

    * ``scope`` : NATIONAL (entityId NULL) ou REGIONAL (entityId = regionId).
    * ``rate`` : Decimal(6,4) — précision rapport IIPE. ``None`` si le
      dénominateur (count_from) vaut 0 (pas de division par zéro).
    * ``sampleSize`` : valeur de count_from au moment du calcul — utile
      pour évaluer la confiance dans le rate.
    * ``isOutlier`` : ``true`` si rate > 2 (redoublement de masse / erreur
      saisie) ou rate < 0 (négatif impossible mais blindé).
    """

    __tablename__ = "TransitionRate"
    __table_args__ = (
        UniqueConstraint(
            "scope", "entityId", "schoolYearFromId",
            "classLevelFrom", "gender",
            name="uq_TransitionRate_scope_entity_year_level_gender",
        ),
        Index(
            "ix_TransitionRate_scope_entityId_schoolYearFromId",
            "scope", "entityId", "schoolYearFromId",
        ),
        Index(
            "ix_TransitionRate_classLevelFrom_classLevelTo",
            "classLevelFrom", "classLevelTo",
        ),
    )

    id: Mapped[str] = cuid_pk()
    schoolYearFromId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False
    )
    schoolYearToId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False
    )
    scope: Mapped[TransitionScope] = mapped_column(
        Enum(TransitionScope, name="TransitionScope", native_enum=True),
        nullable=False,
    )
    # nullable uniquement pour scope=NATIONAL ; le service garantit
    # l'invariant (entityId NOT NULL si scope=REGIONAL).
    entityId: Mapped[str | None] = mapped_column(String(30), nullable=True)
    classLevelFrom: Mapped[EnrollmentClassLevel] = mapped_column(
        Enum(
            EnrollmentClassLevel,
            name="EnrollmentClassLevel",
            native_enum=True,
        ),
        nullable=False,
    )
    classLevelTo: Mapped[EnrollmentClassLevel] = mapped_column(
        Enum(
            EnrollmentClassLevel,
            name="EnrollmentClassLevel",
            native_enum=True,
        ),
        nullable=False,
    )
    gender: Mapped[Gender] = mapped_column(
        Enum(Gender, name="Gender", native_enum=True), nullable=False
    )
    rate: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=6, scale=4), nullable=True
    )
    sampleSize: Mapped[int] = mapped_column(Integer, nullable=False)
    isOutlier: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    computedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    createdById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )

    # Relationships (lazy=raise — convention du codebase).
    schoolYearFrom: Mapped["SchoolYear"] = relationship(
        lazy="raise", foreign_keys=[schoolYearFromId],
    )
    schoolYearTo: Mapped["SchoolYear"] = relationship(
        lazy="raise", foreign_keys=[schoolYearToId],
    )
    createdBy: Mapped["User | None"] = relationship(lazy="raise")


__all__ = ["TransitionRate"]
