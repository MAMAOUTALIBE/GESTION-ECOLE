"""Module 1A — Modèle SQLAlchemy Enrollment.

Une row par (schoolYear × school × classLevel × gender × source) — c'est la
fondation de :
* Dashboard équité (Module 1D)
* Indice de parité fille/garçon GPI (Module 1B)
* Projections par cohorte (Phase 2)

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
* (schoolYearId, schoolId) : lookups par école/année (cas dominant UI saisie).
* (schoolYearId, classLevel, gender) : agrégations nationales (équité fille
  par niveau).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.enrollment.enums import EnrollmentClassLevel, EnrollmentSource
from app.shared.base import Base, TimestampMixin, cuid_pk
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


__all__ = ["Enrollment"]
