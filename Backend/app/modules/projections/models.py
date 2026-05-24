"""Module 2A + 2B — Modèles SQLAlchemy des Projections.

Trois tables :

* ``TransitionRate`` (2A) — taux de transition par cohortes.
* ``ProjectionScenario`` (2B) — paramétrage d'une projection
  (taux de croissance démographique, surcharges de transition rates).
* ``ProjectedEnrollment`` (2B) — effectifs projetés horizon 1..10 ans.

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

Index & unique TransitionRate
-----------------------------
* ``(scope, entityId, schoolYearFromId)`` : "tous les rates d'une région
  pour une année donnée" — vue dashboard équité.
* ``(classLevelFrom, classLevelTo)`` : tri/filtre par paire de niveaux.
* Unique ``(scope, entityId, schoolYearFromId, classLevelFrom, gender)`` :
  garantit l'upsert idempotent au recalcul.

Index & unique ProjectedEnrollment
----------------------------------
* ``(baseSchoolYearId, projectedYear, scope, entityId)`` — vue dashboard.
* ``(scenarioId)`` — comparaison entre scénarios.
* Unique ``(baseSchoolYearId, projectedYear, scope, entityId,
  classLevel, gender, scenarioId)`` — upsert idempotent.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.enrollment.enums import EnrollmentClassLevel
from app.modules.projections.enums import (
    CapacityScope,
    CapacitySeverity,
    RecommendationStatus,
    StaffingSeverity,
    TransitionScope,
)
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


# ---------------------------------------------------------------------------
# Module 2B — ProjectionScenario
# ---------------------------------------------------------------------------
class ProjectionScenario(Base):
    """Paramètres d'un scénario de projection horizon multi-années.

    * ``id`` : cuid (sauf le scénario par défaut BASELINE seedé en migration).
    * ``name`` : nom court, unique. Ex. "BASELINE", "OPTIMISTE_FILLES_2030".
    * ``demographicGrowthRate`` : taux annuel appliqué à MATERNELLE_1.
      Decimal(5,4). Par défaut 2.4 % (INS Guinée).
    * ``customTransitionRates`` (JSONB nullable) : surcharge optionnelle des
      taux de transition pour simulations "what-if". Forme attendue :
      ``{"CP1->CP2:FEMALE": 0.95, ...}``. Si NULL → rates Module 2A
      utilisés directement.

    Pourquoi un seed BASELINE ?
    ---------------------------
    Permet à l'API de référencer ``scenarioId='BASELINE'`` par défaut sans
    forcer le client à créer un scénario explicite avant chaque projection.
    """

    __tablename__ = "ProjectionScenario"

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True,
    )
    description: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    demographicGrowthRate: Mapped[Decimal] = mapped_column(
        Numeric(precision=5, scale=4),
        nullable=False,
        default=Decimal("0.0240"),
        server_default="0.0240",
    )
    # JSONB sur PostgreSQL, JSON ailleurs (cohérence avec opendata/anomalies).
    customTransitionRates: Mapped[Any | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    createdById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True,
    )
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    createdBy: Mapped["User | None"] = relationship(lazy="raise")


# ---------------------------------------------------------------------------
# Module 2B — ProjectedEnrollment
# ---------------------------------------------------------------------------
class ProjectedEnrollment(Base, CreatedAtMixin):
    """Effectifs projetés pour une cellule (région × niveau × genre × année).

    * ``baseSchoolYearId`` : année source des effectifs initiaux
      (CENSUS_DECLARED) sur laquelle on a appliqué les rates.
    * ``projectedYear`` : année cible (int, ex. 2028) — INT plutôt qu'une
      FK SchoolYear car les années projetées peuvent ne pas exister encore.
    * ``scope`` : NATIONAL (entityId NULL) ou REGIONAL (entityId = regionId).
    * ``projectedCount`` : effectifs projetés arrondis à l'entier (INT, pas
      Decimal — on parle d'élèves, pas de moyennes).
    * ``scenarioId`` : NOT NULL avec default 'BASELINE'. Permet de garder
      plusieurs projections en parallèle (cabinet ministre veut comparer
      des hypothèses).
    """

    __tablename__ = "ProjectedEnrollment"
    __table_args__ = (
        UniqueConstraint(
            "baseSchoolYearId", "projectedYear", "scope", "entityId",
            "classLevel", "gender", "scenarioId",
            name="uq_ProjectedEnrollment_full",
        ),
        Index(
            "ix_ProjectedEnrollment_base_year_scope_entity",
            "baseSchoolYearId", "projectedYear", "scope", "entityId",
        ),
        Index(
            "ix_ProjectedEnrollment_scenarioId",
            "scenarioId",
        ),
    )

    id: Mapped[str] = cuid_pk()
    baseSchoolYearId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False,
    )
    projectedYear: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[TransitionScope] = mapped_column(
        Enum(TransitionScope, name="TransitionScope", native_enum=True),
        nullable=False,
    )
    entityId: Mapped[str | None] = mapped_column(String(30), nullable=True)
    classLevel: Mapped[EnrollmentClassLevel] = mapped_column(
        Enum(
            EnrollmentClassLevel, name="EnrollmentClassLevel",
            native_enum=True,
        ),
        nullable=False,
    )
    gender: Mapped[Gender] = mapped_column(
        Enum(Gender, name="Gender", native_enum=True), nullable=False,
    )
    projectedCount: Mapped[int] = mapped_column(Integer, nullable=False)
    scenarioId: Mapped[str] = mapped_column(
        String(30), ForeignKey("ProjectionScenario.id"),
        nullable=False, default="BASELINE", server_default="BASELINE",
    )
    computedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    baseSchoolYear: Mapped["SchoolYear"] = relationship(lazy="raise")
    scenario: Mapped[ProjectionScenario] = relationship(lazy="raise")


# ---------------------------------------------------------------------------
# Module 2C — CapacityDemandSnapshot
# ---------------------------------------------------------------------------
class CapacityDemandSnapshot(Base, CreatedAtMixin):
    """Snapshot capacité vs demande projetée pour (école|préfecture|région|national).

    * ``capacity`` : nombre de places disponibles (= classroomsUsable × norme MEN).
      Pour les agrégats (PREFECTURE, REGIONAL, NATIONAL), c'est la somme des
      capacités des écoles enfants.
    * ``demand`` : effectifs projetés tous niveaux/genres confondus pour
      l'année ``projectedYear``. Au scope SCHOOL, on redistribue la projection
      régionale (Module 2B est calculé au scope REGIONAL/NATIONAL) au prorata
      des effectifs CENSUS_DECLARED de l'année de base (méthode IIPE simple).
    * ``gap = demand - capacity`` (entier ; négatif = marge).
    * ``saturationPct`` : ``demand / capacity × 100`` ; NULL si capacity = 0.
    * ``severity`` : OK / WARNING / CRITICAL (cf. ``compute_severity``).

    Idempotence
    -----------
    Unique ``(baseSchoolYearId, projectedYear, scope, entityId, scenarioId)``
    — un recalcul écrase l'ancien snapshot via delete-then-insert dans le
    service.
    """

    __tablename__ = "CapacityDemandSnapshot"
    __table_args__ = (
        UniqueConstraint(
            "baseSchoolYearId", "projectedYear", "scope",
            "entityId", "scenarioId",
            name="uq_CapacityDemandSnapshot_full",
        ),
        Index(
            "ix_CapacityDemandSnapshot_base_year_scope_severity",
            "baseSchoolYearId", "projectedYear", "scope", "severity",
        ),
        Index(
            "ix_CapacityDemandSnapshot_entityId_computedAt",
            "entityId", "computedAt",
        ),
    )

    id: Mapped[str] = cuid_pk()
    baseSchoolYearId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False,
    )
    projectedYear: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[CapacityScope] = mapped_column(
        Enum(CapacityScope, name="CapacityScope", native_enum=True),
        nullable=False,
    )
    # entityId NULL uniquement pour scope=NATIONAL — invariant enforcé par
    # le service (CapacityDemandService.compute_capacity_demand).
    entityId: Mapped[str | None] = mapped_column(String(30), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    demand: Mapped[int] = mapped_column(Integer, nullable=False)
    gap: Mapped[int] = mapped_column(Integer, nullable=False)
    saturationPct: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=6, scale=2), nullable=True,
    )
    severity: Mapped[CapacitySeverity] = mapped_column(
        Enum(CapacitySeverity, name="CapacitySeverity", native_enum=True),
        nullable=False,
    )
    scenarioId: Mapped[str] = mapped_column(
        String(30), ForeignKey("ProjectionScenario.id"),
        nullable=False, default="BASELINE", server_default="BASELINE",
    )
    computedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    baseSchoolYear: Mapped["SchoolYear"] = relationship(lazy="raise")
    scenario: Mapped[ProjectionScenario] = relationship(lazy="raise")


# ---------------------------------------------------------------------------
# Module 2D — Teacher staffing & transfer recommendations
# ---------------------------------------------------------------------------
class TeacherStaffingSnapshot(Base, CreatedAtMixin):
    """Snapshot dotation enseignants d'une école pour une année scolaire.

    * ``studentsCount`` / ``teachersCount`` — comptages au moment du calcul
      (count Student et Teacher.status=APPROVED par schoolId).
    * ``ratio = studentsCount / teachersCount`` (Decimal(8,2)). NULL si
      ``teachersCount = 0`` — école sans enseignant, classée CRITICAL.
    * ``severity`` — OVER_STAFFED / ADEQUATE / UNDER_STAFFED / CRITICAL.
    * ``expectedTeachers`` — ``math.ceil(students / NORM)``.
    * ``gap = expectedTeachers - teachersCount`` (int signé). Négatif =
      sur-doté, positif = besoin d'enseignants.

    Idempotence : unique ``(schoolYearId, schoolId)`` — un recalcul écrase
    via delete-then-insert.
    """

    __tablename__ = "TeacherStaffingSnapshot"
    __table_args__ = (
        UniqueConstraint(
            "schoolYearId", "schoolId",
            name="uq_TeacherStaffingSnapshot_year_school",
        ),
        Index(
            "ix_TeacherStaffingSnapshot_year_severity",
            "schoolYearId", "severity",
        ),
    )

    id: Mapped[str] = cuid_pk()
    schoolYearId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False,
    )
    schoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False,
    )
    studentsCount: Mapped[int] = mapped_column(Integer, nullable=False)
    teachersCount: Mapped[int] = mapped_column(Integer, nullable=False)
    ratio: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=8, scale=2), nullable=True,
    )
    severity: Mapped[StaffingSeverity] = mapped_column(
        Enum(StaffingSeverity, name="StaffingSeverity", native_enum=True),
        nullable=False,
    )
    expectedTeachers: Mapped[int] = mapped_column(Integer, nullable=False)
    gap: Mapped[int] = mapped_column(Integer, nullable=False)
    computedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )


class TeacherTransferRecommendation(Base):
    """Recommandation consultative de transfert d'enseignants.

    Paire ``(fromSchoolId, toSchoolId)`` + ``transfersSuggested``. Pas
    d'auto-exécution : ces lignes alimentent une vue cabinet/RH qui les
    valide ou rejette explicitement (audit trail dans ``reviewedById``,
    ``reviewedAt``, ``reviewNote``).

    ``priorityScore`` — Decimal(6,2). Calcul : voir
    ``staffing.compute_priority_score`` (bonus si même préfecture).

    ``rationale`` — texte libre explicatif (généré par le service au
    moment du calcul). Aide les RH à comprendre la suggestion sans
    re-lire les ratios source.

    ``status`` — workflow PENDING → REVIEWED/ACCEPTED/REJECTED/EXECUTED.
    """

    __tablename__ = "TeacherTransferRecommendation"
    __table_args__ = (
        Index(
            "ix_TeacherTransferRecommendation_region_priority",
            "regionId", "priorityScore",
        ),
        Index(
            "ix_TeacherTransferRecommendation_status",
            "status",
        ),
        Index(
            "ix_TeacherTransferRecommendation_year",
            "schoolYearId",
        ),
    )

    id: Mapped[str] = cuid_pk()
    schoolYearId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False,
    )
    fromSchoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False,
    )
    toSchoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False,
    )
    prefectureId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Prefecture.id"), nullable=True,
    )
    regionId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Region.id"), nullable=False,
    )
    transfersSuggested: Mapped[int] = mapped_column(Integer, nullable=False)
    priorityScore: Mapped[Decimal] = mapped_column(
        Numeric(precision=6, scale=2), nullable=False,
    )
    rationale: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[RecommendationStatus] = mapped_column(
        Enum(
            RecommendationStatus,
            name="RecommendationStatus",
            native_enum=True,
        ),
        nullable=False,
        default=RecommendationStatus.PENDING,
        server_default="PENDING",
    )
    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    reviewedById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True,
    )
    reviewedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    reviewNote: Mapped[str | None] = mapped_column(String, nullable=True)


__all__ = [
    "CapacityDemandSnapshot",
    "ProjectedEnrollment",
    "ProjectionScenario",
    "TeacherStaffingSnapshot",
    "TeacherTransferRecommendation",
    "TransitionRate",
]
