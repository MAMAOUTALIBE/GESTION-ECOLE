"""Module 1A + 1B — Modèles SQLAlchemy Enrollment / GpiSnapshot.

Une row ``Enrollment`` par (schoolYear × school × classLevel × gender × source).
Une row ``GpiSnapshot`` par (schoolYear × scope × entity) — fondation du
Module 1B (Gender Parity Index, alertes auto + comparaison annuelle).

Pourquoi pas calculer "live" depuis Student ?
----------------------------------------------
* La table Student est partielle (recensement annuel != flux temps réel).
* En milieu rural, la fiche élève peut prendre plusieurs mois à arriver.
* Pour la projection cohorte sur 6 ans, on a besoin d'un historique stable
  même après purges/anonymisations RGPD côté Student.
* Le déclaratif (CENSUS_DECLARED) est la SOURCE DE VÉRITÉ pour le pilotage —
  les écarts avec COMPUTED_FROM_STUDENTS sont des signaux data quality.

Index
-----
* Enrollment : (schoolYearId, schoolId) — saisie UI ; (schoolYearId,
  classLevel, gender) — agrégats nationaux.
* GpiSnapshot : (schoolYearId, scope, severity) — points chauds ;
  (entityId, computedAt DESC) — séries temporelles.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
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

from app.modules.enrollment.enums import (
    EnrollmentClassLevel,
    EnrollmentSource,
    GpiScope,
)
from app.modules.enrollment.parity import GpiSeverity
from app.shared.base import Base, CreatedAtMixin, TimestampMixin, cuid_pk
from app.shared.enums import Gender

if TYPE_CHECKING:
    from app.modules.academics.models import SchoolYear
    from app.modules.auth.models import User
    from app.modules.schools.models import School


class Enrollment(Base, TimestampMixin):
    """Effectif désagrégé (niveau × genre) déclaré par une école pour une
    année scolaire donnée.
    """

    __tablename__ = "Enrollment"
    __table_args__ = (
        # Une seule mesure par (year, school, level, gender, source) — autorise
        # la coexistence d'une CENSUS_DECLARED et d'une COMPUTED_FROM_STUDENTS
        # pour la même cellule (cross-check data quality).
        UniqueConstraint(
            "schoolYearId",
            "schoolId",
            "classLevel",
            "gender",
            "source",
            name="uq_Enrollment_year_school_level_gender_source",
        ),
        Index(
            "ix_Enrollment_schoolYearId_schoolId",
            "schoolYearId",
            "schoolId",
        ),
        Index(
            "ix_Enrollment_schoolYearId_classLevel_gender",
            "schoolYearId",
            "classLevel",
            "gender",
        ),
    )

    id: Mapped[str] = cuid_pk()
    schoolYearId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False
    )
    schoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False
    )
    classLevel: Mapped[EnrollmentClassLevel] = mapped_column(
        Enum(EnrollmentClassLevel, name="EnrollmentClassLevel", native_enum=True),
        nullable=False,
    )
    gender: Mapped[Gender] = mapped_column(
        Enum(Gender, name="Gender", native_enum=True), nullable=False
    )
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[EnrollmentSource] = mapped_column(
        Enum(EnrollmentSource, name="EnrollmentSource", native_enum=True),
        nullable=False,
    )
    recordedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recordedById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Relationships (lazy=raise — same convention as the rest of the codebase).
    schoolYear: Mapped["SchoolYear"] = relationship(lazy="raise")
    school: Mapped["School"] = relationship(lazy="raise")
    recordedBy: Mapped["User | None"] = relationship(lazy="raise")


# ---------------------------------------------------------------------------
# Module 1B — Snapshot GPI
# ---------------------------------------------------------------------------
class GpiSnapshot(Base, CreatedAtMixin):
    """Snapshot d'un GPI à un instant donné, pour un scope donné.

    * ``scope`` : NATIONAL / REGIONAL / PREFECTURE / SCHOOL.
    * ``entityId`` : nullable uniquement quand scope = NATIONAL ; sinon
      contient l'id de la région / préfecture / école.
    * ``gpi`` : Decimal(6,4) — précision rapport gouvernemental. ``None``
      si la cohorte est vide (girls=0 AND boys=0).
    * ``severity`` : pré-calculé via ``parity.classify_gpi`` au moment du
      snapshot pour permettre un filtrage indexé "WHERE severity = CRITICAL_GIRLS".
    """

    __tablename__ = "GpiSnapshot"
    __table_args__ = (
        Index(
            "ix_GpiSnapshot_schoolYearId_scope_severity",
            "schoolYearId", "scope", "severity",
        ),
        Index(
            "ix_GpiSnapshot_entityId_computedAt",
            "entityId", "computedAt",
        ),
    )

    id: Mapped[str] = cuid_pk()
    schoolYearId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False
    )
    scope: Mapped[GpiScope] = mapped_column(
        Enum(GpiScope, name="GpiScope", native_enum=True), nullable=False
    )
    # nullable uniquement pour scope=NATIONAL ; le service garantit
    # l'invariant (entityId NOT NULL si scope != NATIONAL).
    entityId: Mapped[str | None] = mapped_column(String(30), nullable=True)
    girlsCount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    boysCount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gpi: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=6, scale=4), nullable=True
    )
    severity: Mapped[GpiSeverity] = mapped_column(
        Enum(GpiSeverity, name="GpiSeverity", native_enum=True),
        nullable=False,
        default=GpiSeverity.NORMAL,
    )
    computedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


__all__ = ["Enrollment", "GpiScope", "GpiSnapshot"]
