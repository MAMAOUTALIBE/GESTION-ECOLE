"""Module 3C — Service du score d'investissement.

Responsabilités :

* ``compute_priority_scores`` — recalcule le score de toutes les écoles
  APPROVED et persiste (upsert idempotent par école).
* ``list_priorities`` — lecture filtrable (RBAC scope appliqué).
* ``top_priorities`` — top N par totalScore desc (par défaut 100).
* ``get_school_priority`` — détail breakdown pour une école.

Sources de données (read-only) :

* ``School`` — caractéristiques infrastructure + position + zoneType.
* ``Enrollment`` (CENSUS_DECLARED) — calcul GPI école.
* ``CapacityDemandSnapshot`` (scope=SCHOOL, horizon le plus proche) —
  sévérité de saturation projetée.
* ``SubPrefecture.defaultZoneType`` — fallback de la zone effective.

RBAC
----
* Calcul (``compute_priority_scores``) : NATIONAL / MINISTRY uniquement
  (action coûteuse + à portée nationale).
* Lecture : tout user authentifié, filtré par scope (REGIONAL = sa
  région, PREFECTURE = sa préfecture, SCHOOL = uniquement son école).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.academics.models import SchoolYear
from app.modules.auth.models import User
from app.modules.enrollment.enums import EnrollmentSource
from app.modules.enrollment.models import Enrollment
from app.modules.investment.enums import PriorityCategory
from app.modules.investment.models import InvestmentPriorityScore
from app.modules.investment.schemas import (
    ComputeScoresResponse,
    InvestmentScoreRead,
)
from app.modules.investment.scoring import (
    classify,
    compute_total,
    score_accessibility,
    score_equity,
    score_infrastructure,
    score_saturation,
)
from app.modules.projections.enums import CapacityScope, CapacitySeverity
from app.modules.projections.models import CapacityDemandSnapshot
from app.modules.schools.models import School
from app.modules.territory.models import Region, SubPrefecture
from app.shared.base import generate_cuid
from app.shared.enums import Gender, UserRole, ValidationStatus, ZoneType
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SCHOOL_SCOPE_ROLES,
)

# Roles autorisés à déclencher un recalcul global. La lecture reste
# ouverte à tout user authentifié (filtre scope appliqué a posteriori).
INVESTMENT_COMPUTE_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN}
)


class InvestmentService:
    """Calcule et expose les scores d'investissement par école."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # COMPUTE
    # ==================================================================
    async def compute_priority_scores(
        self,
        base_school_year_id: str,
        actor: User,
    ) -> ComputeScoresResponse:
        """Recalcule tous les scores et persiste (upsert par schoolId).

        RBAC : NATIONAL_ADMIN / MINISTRY_ADMIN uniquement.
        """
        self._ensure_compute_role(actor)

        # Validation année source.
        year_stmt = select(SchoolYear).where(SchoolYear.id == base_school_year_id)
        year = (await self.session.execute(year_stmt)).scalars().one_or_none()
        if year is None:
            raise NotFoundError(
                detail=f"SchoolYear introuvable : {base_school_year_id}",
            )

        # 1. Charge toutes les écoles APPROVED + leur subPref pour la zone.
        schools_stmt = (
            select(
                School.id,
                School.waterSource,
                School.electricitySource,
                School.toiletsBoys,
                School.toiletsGirls,
                School.classroomsTotal,
                School.classroomsUsable,
                School.buildingCondition,
                School.internetAvailable,
                School.zoneType,
                School.subPrefectureId,
                SubPrefecture.defaultZoneType.label("subDefaultZone"),
            )
            .outerjoin(
                SubPrefecture,
                SubPrefecture.id == School.subPrefectureId,
            )
            .where(School.status == ValidationStatus.APPROVED)
        )
        school_rows = (await self.session.execute(schools_stmt)).all()

        # 2. Charge les sévérités saturation à l'horizon le plus proche.
        severity_by_school = await self._load_saturation_severity_by_school(
            base_school_year_id,
        )

        # 3. Charge le GPI école depuis Enrollment (CENSUS_DECLARED).
        gpi_by_school = await self._load_gpi_by_school(base_school_year_id)

        # 4. Pour chaque école : score les 4 dimensions, classifie, upsert.
        now = datetime.now(UTC)

        # On efface les anciens scores (upsert global "delete then insert").
        # Cette approche est cohérente avec Module 2C (CapacityDemand) qui
        # adopte la même idempotence.
        await self.session.execute(delete(InvestmentPriorityScore))

        category_counter: Counter[str] = Counter()

        for row in school_rows:
            school_data = {
                "waterSource": row.waterSource,
                "electricitySource": row.electricitySource,
                "toiletsBoys": row.toiletsBoys,
                "toiletsGirls": row.toiletsGirls,
                "classroomsTotal": row.classroomsTotal,
                "classroomsUsable": row.classroomsUsable,
                "buildingCondition": row.buildingCondition,
                "internetAvailable": row.internetAvailable,
            }
            infra_score, infra_details = score_infrastructure(school_data)
            sat_score, sat_details = score_saturation(
                severity_by_school.get(row.id),
            )
            eq_score, eq_details = score_equity(gpi_by_school.get(row.id))

            # Zone effective : override école sinon défaut sous-préf, sinon RURAL.
            effective_zone: ZoneType
            if row.zoneType is not None:
                effective_zone = row.zoneType
            elif row.subDefaultZone is not None:
                effective_zone = row.subDefaultZone
            else:
                effective_zone = ZoneType.RURAL
            # Distance avg : non-calculée à ce stade (Module 5 hook futur).
            acc_score, acc_details = score_accessibility(
                effective_zone, avg_distance_km=None,
            )

            total = compute_total([infra_score, sat_score, eq_score, acc_score])
            category = classify(total)
            category_counter[category.value] += 1

            breakdown = {
                "infrastructure": infra_details,
                "saturation": sat_details,
                "equity": eq_details,
                "accessibility": acc_details,
            }
            self.session.add(
                InvestmentPriorityScore(
                    id=generate_cuid(),
                    schoolId=row.id,
                    baseSchoolYearId=base_school_year_id,
                    infrastructureScore=infra_score,
                    saturationScore=sat_score,
                    equityScore=eq_score,
                    accessibilityScore=acc_score,
                    totalScore=total,
                    priorityCategory=category,
                    computedAt=now,
                    breakdownJson=breakdown,
                )
            )

        await self.session.flush()

        return ComputeScoresResponse(
            scoresComputed=len(school_rows),
            byCategory={
                # On expose toutes les clefs même à 0 pour faciliter la
                # vue UI / dashboard.
                PriorityCategory.TRES_HAUTE.value: category_counter.get(
                    PriorityCategory.TRES_HAUTE.value, 0,
                ),
                PriorityCategory.HAUTE.value: category_counter.get(
                    PriorityCategory.HAUTE.value, 0,
                ),
                PriorityCategory.MOYENNE.value: category_counter.get(
                    PriorityCategory.MOYENNE.value, 0,
                ),
                PriorityCategory.BASSE.value: category_counter.get(
                    PriorityCategory.BASSE.value, 0,
                ),
            },
            baseSchoolYearId=base_school_year_id,
            computedAt=now,
        )

    # ==================================================================
    # READ
    # ==================================================================
    async def list_priorities(
        self,
        actor: User,
        *,
        category: PriorityCategory | None = None,
        region_id: str | None = None,
        base_school_year_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[InvestmentScoreRead]:
        """Liste paginée + filtres + RBAC scope appliqué."""
        stmt = (
            select(
                InvestmentPriorityScore,
                School.name.label("schoolName"),
                School.regionId.label("regionId"),
                Region.name.label("regionName"),
            )
            .join(School, School.id == InvestmentPriorityScore.schoolId)
            .outerjoin(Region, Region.id == School.regionId)
        )
        stmt = self._apply_scope(stmt, actor)

        if category is not None:
            stmt = stmt.where(
                InvestmentPriorityScore.priorityCategory == category,
            )
        if region_id is not None:
            stmt = stmt.where(School.regionId == region_id)
        if base_school_year_id is not None:
            stmt = stmt.where(
                InvestmentPriorityScore.baseSchoolYearId == base_school_year_id,
            )
        stmt = (
            stmt.order_by(InvestmentPriorityScore.totalScore.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).all()
        return [self._row_to_read(r) for r in rows]

    async def top_priorities(
        self,
        actor: User,
        limit: int = 100,
    ) -> list[InvestmentScoreRead]:
        """Top N par totalScore desc, scope RBAC inclus."""
        stmt = (
            select(
                InvestmentPriorityScore,
                School.name.label("schoolName"),
                School.regionId.label("regionId"),
                Region.name.label("regionName"),
            )
            .join(School, School.id == InvestmentPriorityScore.schoolId)
            .outerjoin(Region, Region.id == School.regionId)
        )
        stmt = self._apply_scope(stmt, actor)
        stmt = stmt.order_by(
            InvestmentPriorityScore.totalScore.desc(),
        ).limit(limit)
        rows = (await self.session.execute(stmt)).all()
        return [self._row_to_read(r) for r in rows]

    async def get_school_priority(
        self,
        school_id: str,
        actor: User,
    ) -> InvestmentScoreRead:
        """Détail breakdown pour une école. 404 si pas de score calculé."""
        stmt = (
            select(
                InvestmentPriorityScore,
                School.name.label("schoolName"),
                School.regionId.label("regionId"),
                Region.name.label("regionName"),
            )
            .join(School, School.id == InvestmentPriorityScore.schoolId)
            .outerjoin(Region, Region.id == School.regionId)
            .where(InvestmentPriorityScore.schoolId == school_id)
        )
        stmt = self._apply_scope(stmt, actor)
        row = (await self.session.execute(stmt)).one_or_none()
        if row is None:
            raise NotFoundError(
                detail=(
                    f"Aucun score d'investissement pour l'école {school_id} "
                    "(ou hors périmètre)."
                ),
            )
        return self._row_to_read(row)

    # ==================================================================
    # Helpers privés
    # ==================================================================
    def _ensure_compute_role(self, actor: User) -> None:
        if actor.role not in INVESTMENT_COMPUTE_ROLES:
            raise ForbiddenError(
                detail=(
                    "Seuls les administrateurs nationaux et le cabinet "
                    "peuvent déclencher le calcul des scores."
                ),
                extra={
                    "required_any_of": sorted(
                        r.value for r in INVESTMENT_COMPUTE_ROLES
                    ),
                },
            )

    def _apply_scope(self, stmt: Any, actor: User) -> Any:
        """Filtre par scope territorial de l'acteur.

        * NATIONAL / MINISTRY : pas de filtre.
        * REGIONAL_*          : filtre regionId.
        * PREFECTURE          : filtre prefectureId.
        * SCHOOL / autre      : filtre schoolId == user.schoolId (ou
          aucune ligne si schoolId NULL).
        """
        role = actor.role
        if role in NATIONAL_SCOPE_ROLES:
            return stmt
        if role in REGIONAL_SCOPE_ROLES and actor.regionId:
            return stmt.where(School.regionId == actor.regionId)
        if role in PREFECTURE_SCOPE_ROLES and actor.prefectureId:
            return stmt.where(School.prefectureId == actor.prefectureId)
        if role in SCHOOL_SCOPE_ROLES and getattr(actor, "schoolId", None):
            return stmt.where(School.id == actor.schoolId)
        # Aucun scope déterminable : on coupe à vide.
        return stmt.where(School.id == "__none__")

    @staticmethod
    def _row_to_read(row: Any) -> InvestmentScoreRead:
        score: InvestmentPriorityScore = row[0]
        return InvestmentScoreRead(
            schoolId=score.schoolId,
            schoolName=row.schoolName,
            regionId=row.regionId,
            regionName=row.regionName,
            baseSchoolYearId=score.baseSchoolYearId,
            infrastructureScore=score.infrastructureScore,
            saturationScore=score.saturationScore,
            equityScore=score.equityScore,
            accessibilityScore=score.accessibilityScore,
            totalScore=score.totalScore,
            priorityCategory=score.priorityCategory,
            computedAt=score.computedAt,
            breakdownJson=score.breakdownJson,
        )

    async def _load_saturation_severity_by_school(
        self, base_school_year_id: str,
    ) -> dict[str, CapacitySeverity]:
        """Mappe schoolId -> severity, prend l'horizon +1 (min projectedYear).

        Cohérent avec la logique cockpit ``_count_projected_critical_schools``
        qui cible le min(projectedYear) du scope SCHOOL.
        """
        # Min projected year — sous-requête.
        min_year_stmt = (
            select(func.min(CapacityDemandSnapshot.projectedYear))
            .where(
                CapacityDemandSnapshot.scope == CapacityScope.SCHOOL,
                CapacityDemandSnapshot.baseSchoolYearId == base_school_year_id,
            )
        )
        min_year = (
            await self.session.execute(min_year_stmt)
        ).scalar_one_or_none()
        if min_year is None:
            return {}

        stmt = (
            select(
                CapacityDemandSnapshot.entityId,
                CapacityDemandSnapshot.severity,
            )
            .where(
                CapacityDemandSnapshot.scope == CapacityScope.SCHOOL,
                CapacityDemandSnapshot.baseSchoolYearId == base_school_year_id,
                CapacityDemandSnapshot.projectedYear == min_year,
            )
        )
        rows = (await self.session.execute(stmt)).all()
        out: dict[str, CapacitySeverity] = {}
        for entity_id, severity in rows:
            if entity_id is not None:
                out[entity_id] = severity
        return out

    async def _load_gpi_by_school(
        self, base_school_year_id: str,
    ) -> dict[str, Decimal | None]:
        """Calcule GPI = filles/garçons par école depuis Enrollment.

        Source : ``EnrollmentSource.CENSUS_DECLARED`` (la vérité officielle).
        ``None`` si garçons = 0 (évite division par zéro — école sans
        garçons est un cas extrême qui mérite un signal mais pas une div0).
        """
        stmt = (
            select(
                Enrollment.schoolId,
                Enrollment.gender,
                func.coalesce(func.sum(Enrollment.count), 0).label("total"),
            )
            .where(
                and_(
                    Enrollment.schoolYearId == base_school_year_id,
                    Enrollment.source == EnrollmentSource.CENSUS_DECLARED,
                )
            )
            .group_by(Enrollment.schoolId, Enrollment.gender)
        )
        rows = (await self.session.execute(stmt)).all()
        accum: dict[str, dict[Gender, int]] = defaultdict(
            lambda: {Gender.FEMALE: 0, Gender.MALE: 0},
        )
        for school_id, gender, total in rows:
            if gender in (Gender.FEMALE, Gender.MALE):
                accum[school_id][gender] += int(total)
        out: dict[str, Decimal | None] = {}
        for school_id, counts in accum.items():
            girls = counts[Gender.FEMALE]
            boys = counts[Gender.MALE]
            if boys <= 0:
                out[school_id] = None
                continue
            out[school_id] = (Decimal(girls) / Decimal(boys)).quantize(
                Decimal("0.0001"),
            )
        return out


__all__ = ["INVESTMENT_COMPUTE_ROLES", "InvestmentService"]
