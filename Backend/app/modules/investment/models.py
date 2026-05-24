"""Module 3C — Modèle SQLAlchemy du score d'investissement par école.

Une seule table : ``InvestmentPriorityScore`` (un row par école, écrasé
en place à chaque recalcul via UPSERT logique).

* ``schoolId`` UNIQUE : un score à la fois par école — un recalcul
  remplace le score précédent.
* ``breakdownJson`` JSONB : stocke les détails par dimension (valeurs
  brutes, points attribués, pondérations) pour audit / UI de détail.

Pourquoi pas garder l'historique (un row par recalcul) ?
---------------------------------------------------------
À ce stade, le cabinet veut un classement « actuel ». L'historique est
gérable plus tard via une table de snapshots si besoin (cf. Module 19
``CockpitKpiSnapshot`` qui historise déjà la statistique nationale).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.investment.enums import PriorityCategory
from app.shared.base import Base, CreatedAtMixin, cuid_pk

if TYPE_CHECKING:
    from app.modules.academics.models import SchoolYear
    from app.modules.schools.models import School


class InvestmentPriorityScore(Base, CreatedAtMixin):
    """Score composite de priorité d'investissement d'une école.

    Champs :

    * ``id`` (cuid) — primary key.
    * ``schoolId`` (FK School) — UNIQUE : un seul score actif par école.
    * ``baseSchoolYearId`` — année de référence des données sources.
    * ``infrastructureScore`` / ``saturationScore`` / ``equityScore`` /
      ``accessibilityScore`` — sous-scores pondérés (INT).
    * ``totalScore`` — somme des 4 (0..100).
    * ``priorityCategory`` — classification finale.
    * ``computedAt`` — horodatage du calcul.
    * ``breakdownJson`` JSONB — détail audit par dimension.
    """

    __tablename__ = "InvestmentPriorityScore"
    __table_args__ = (
        Index(
            "ix_InvestmentPriorityScore_totalScore",
            "totalScore",
        ),
        Index(
            "ix_InvestmentPriorityScore_priorityCategory",
            "priorityCategory",
        ),
        Index(
            "ix_InvestmentPriorityScore_baseSchoolYearId",
            "baseSchoolYearId",
        ),
    )

    id: Mapped[str] = cuid_pk()
    schoolId: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("School.id"),
        nullable=False,
        unique=True,
    )
    baseSchoolYearId: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("SchoolYear.id"),
        nullable=False,
    )
    infrastructureScore: Mapped[int] = mapped_column(Integer, nullable=False)
    saturationScore: Mapped[int] = mapped_column(Integer, nullable=False)
    equityScore: Mapped[int] = mapped_column(Integer, nullable=False)
    accessibilityScore: Mapped[int] = mapped_column(Integer, nullable=False)
    totalScore: Mapped[int] = mapped_column(Integer, nullable=False)
    priorityCategory: Mapped[PriorityCategory] = mapped_column(
        Enum(
            PriorityCategory,
            name="InvestmentPriorityCategory",
            native_enum=True,
        ),
        nullable=False,
    )
    computedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    # JSONB sur PostgreSQL, JSON ailleurs (cohérent avec opendata /
    # simulator / projections).
    breakdownJson: Mapped[Any | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )

    school: Mapped["School"] = relationship(lazy="raise")
    baseSchoolYear: Mapped["SchoolYear"] = relationship(lazy="raise")


__all__ = ["InvestmentPriorityScore"]
