"""Module 2A + 2B — Services du module Projections.

Module 2A — TransitionRate
--------------------------
* ``compute_transitions`` : recalcul + persistance des rates pour une
  liste d'années sources. Idempotent (upsert via unique).
* ``list_rates`` : lecture filtrée + scope RBAC territorial.
* ``get_outliers`` : lecture filtrée sur les rates flaggés ``isOutlier``.

Module 2B — Projections horizon 5 ans
-------------------------------------
* ``ProjectionService.run_projection`` : applique les rates Module 2A
  sur les effectifs CENSUS_DECLARED de l'année de base pour produire
  les projections horizon k=1..N. Idempotent (delete-then-insert par
  ``(baseSchoolYearId, scenarioId)``).
* ``get_projections`` : lecture filtrée + scope RBAC territorial.
* ``create_scenario`` / ``list_scenarios`` : paramétrage des projections.

Toutes les méthodes sont ``async``. Les calculs s'appuient sur les
agrégats ``Enrollment`` (source = ``CENSUS_DECLARED``).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.modules.academics.models import SchoolYear
from app.modules.auth.models import User
from app.modules.enrollment.enums import EnrollmentClassLevel, EnrollmentSource
from app.modules.enrollment.models import Enrollment
from app.modules.projections.capacity import (
    compute_gap,
    compute_saturation_pct,
    compute_school_capacity,
    compute_severity,
)
from app.modules.projections.enums import (
    DEMOGRAPHIC_GROWTH_RATE_DEFAULT,
    STUDENTS_PER_CLASSROOM_NORM,
    CapacityScope,
    CapacitySeverity,
    TransitionScope,
)
from app.modules.projections.models import (
    CapacityDemandSnapshot,
    ProjectedEnrollment,
    ProjectionScenario,
    TransitionRate,
)
from app.modules.projections.projection import (
    EnrollmentMap,
    TransitionRateMap,
    project_one_year,
)
from app.modules.projections.schemas import (
    CapacityDemandFilters,
    CapacityDemandRequest,
    CapacityDemandResponse,
    CapacityDemandRow,
    ComputeTransitionsResponse,
    ProjectedEnrollmentRead,
    ProjectionFilters,
    ProjectionScenarioCreate,
    ProjectionScenarioRead,
    RunProjectionRequest,
    RunProjectionResponse,
    TransitionRateFilters,
    TransitionRateRead,
)
from app.modules.projections.transitions import (
    LEVEL_PAIRS,
    compute_rate,
)
from app.modules.schools.models import School
from app.shared.base import generate_cuid
from app.shared.enums import Gender, UserRole
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
)

# Rôles autorisés à déclencher un recalcul (écriture). On reste strict :
# seuls les admins centraux peuvent figer un snapshot de transitions —
# l'année doit être clôturée par décision MEN.
COMPUTE_TRANSITIONS_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN}
)


class TransitionRateService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # Public API
    # ==================================================================
    async def compute_transitions(
        self,
        school_year_from_ids: list[str],
        actor: User,
    ) -> ComputeTransitionsResponse:
        """Recalcule + persiste les taux de transition pour les années
        données.

        Pour chaque ``schoolYearFromId`` :

        1. Trouve la ``SchoolYear`` qui suit (la plus proche après par
           ``startDate``). Si aucune → skip silencieux.
        2. Récupère les agrégats ``Enrollment`` ``(regionId, classLevel,
           gender)`` pour les 2 années (source = CENSUS_DECLARED).
        3. Pour chaque paire de niveaux successifs + chaque genre :
           - calcule le rate par région (REGIONAL),
           - calcule le rate national (somme pondérée, pas moyenne simple),
           - persiste les rows (upsert via delete + insert dans la même
             transaction pour cette cellule).
        4. Hook Module 9 — crée les anomalies ``TRANSITION_RATE_OUTLIER``
           pour chaque outlier détecté.

        Restreint à NATIONAL_ADMIN / MINISTRY_ADMIN.
        """
        if actor.role not in COMPUTE_TRANSITIONS_ROLES:
            raise ForbiddenError(
                detail=(
                    "Seul un administrateur central peut recalculer les "
                    "taux de transition."
                ),
                extra={
                    "required_any_of": sorted(
                        r.value for r in COMPUTE_TRANSITIONS_ROLES
                    )
                },
            )

        # Validation existence des années sources.
        for year_id in school_year_from_ids:
            existing = (
                await self.session.execute(
                    select(SchoolYear.id).where(SchoolYear.id == year_id)
                )
            ).scalar_one_or_none()
            if existing is None:
                raise NotFoundError(
                    detail=(
                        f"SchoolYear introuvable : {year_id}"
                    )
                )

        now = datetime.now(UTC)
        total_computed = 0
        total_outliers = 0
        skipped: list[str] = []
        # Rates candidats à une anomalie : outlier DB (rate > 2 ou < 0)
        # OU abandon massif (rate < 0.5). Le détecteur fait le filtrage
        # final, on lui passe juste les rows à examiner.
        anomaly_candidates: list[TransitionRate] = []

        for year_from_id in school_year_from_ids:
            year_to = await self._find_next_school_year(year_from_id)
            if year_to is None:
                # Pas de successeur (année courante / dernière). Skip
                # silencieux : c'est attendu pour la dernière year connue.
                skipped.append(year_from_id)
                continue

            # Wipe les rates existants pour cette year_from (idempotence).
            await self.session.execute(
                delete(TransitionRate).where(
                    TransitionRate.schoolYearFromId == year_from_id,
                )
            )

            # Agrège Enrollment pour les 2 années (region, level, gender).
            agg_from = await self._aggregate_by_region_level_gender(
                year_from_id,
            )
            agg_to = await self._aggregate_by_region_level_gender(
                year_to.id,
            )

            region_ids: set[str] = {key[0] for key in agg_from} | {
                key[0] for key in agg_to
            }

            for level_from, level_to in LEVEL_PAIRS:
                for gender in (Gender.FEMALE, Gender.MALE):
                    national_from = 0
                    national_to = 0
                    for region_id in region_ids:
                        count_from = agg_from.get(
                            (region_id, level_from, gender), 0,
                        )
                        count_to = agg_to.get(
                            (region_id, level_to, gender), 0,
                        )
                        # Rate REGIONAL.
                        rate, is_outlier = compute_rate(
                            count_from, count_to,
                        )
                        row = TransitionRate(
                            schoolYearFromId=year_from_id,
                            schoolYearToId=year_to.id,
                            scope=TransitionScope.REGIONAL,
                            entityId=region_id,
                            classLevelFrom=level_from,
                            classLevelTo=level_to,
                            gender=gender,
                            rate=rate,
                            sampleSize=count_from,
                            isOutlier=is_outlier,
                            computedAt=now,
                            createdById=actor.id,
                        )
                        self.session.add(row)
                        total_computed += 1
                        if is_outlier:
                            total_outliers += 1
                        # Anomaly hook : rate > 2 OU rate < 0.5.
                        if rate is not None and (
                            rate > Decimal("2.0") or rate < Decimal("0.5")
                        ):
                            anomaly_candidates.append(row)
                        national_from += count_from
                        national_to += count_to

                    # Rate NATIONAL = somme des numérateurs / somme des
                    # dénominateurs sur toutes régions (pondéré). Une
                    # moyenne simple des rates régionaux biaiserait les
                    # régions à faibles effectifs.
                    national_rate, national_is_outlier = compute_rate(
                        national_from, national_to,
                    )
                    nat_row = TransitionRate(
                        schoolYearFromId=year_from_id,
                        schoolYearToId=year_to.id,
                        scope=TransitionScope.NATIONAL,
                        entityId=None,
                        classLevelFrom=level_from,
                        classLevelTo=level_to,
                        gender=gender,
                        rate=national_rate,
                        sampleSize=national_from,
                        isOutlier=national_is_outlier,
                        computedAt=now,
                        createdById=actor.id,
                    )
                    self.session.add(nat_row)
                    total_computed += 1
                    if national_is_outlier:
                        total_outliers += 1
                    if national_rate is not None and (
                        national_rate > Decimal("2.0")
                        or national_rate < Decimal("0.5")
                    ):
                        anomaly_candidates.append(nat_row)

            await self.session.flush()

        # Hook Module 9 — anomalies pour les outliers (rate > 2 ou < 0.5).
        anomalies_created = await self._create_outlier_anomalies(
            anomaly_candidates,
        )

        return ComputeTransitionsResponse(
            computed=total_computed,
            outliers=total_outliers,
            anomaliesCreated=anomalies_created,
            skipped=skipped,
            computedAt=now,
        )

    async def list_rates(
        self,
        filters: TransitionRateFilters,
        scope_user: User,
    ) -> list[TransitionRateRead]:
        """Liste les rates persistés, filtrage + scope RBAC territorial.

        Un REGIONAL_ADMIN ne voit que les rates de sa région
        (scope=REGIONAL et entityId=user.regionId) + les NATIONAL.
        """
        stmt = select(TransitionRate)

        if filters.scope is not None:
            stmt = stmt.where(TransitionRate.scope == filters.scope)
        if filters.entityId is not None:
            stmt = stmt.where(TransitionRate.entityId == filters.entityId)
        if filters.schoolYearFromId is not None:
            stmt = stmt.where(
                TransitionRate.schoolYearFromId == filters.schoolYearFromId,
            )
        if filters.classLevelFrom is not None:
            stmt = stmt.where(
                TransitionRate.classLevelFrom == filters.classLevelFrom,
            )
        if filters.gender is not None:
            stmt = stmt.where(TransitionRate.gender == filters.gender)

        stmt = self._apply_territorial_scope(stmt, scope_user)

        stmt = stmt.order_by(
            TransitionRate.scope.asc(),
            TransitionRate.entityId.asc(),
            TransitionRate.classLevelFrom.asc(),
            TransitionRate.gender.asc(),
        )

        rows = (await self.session.execute(stmt)).scalars().all()
        return [TransitionRateRead.model_validate(r) for r in rows]

    async def get_outliers(
        self,
        scope_user: User,
        *,
        school_year_from_id: str | None = None,
    ) -> list[TransitionRateRead]:
        """Renvoie uniquement les rates flaggés ``isOutlier=True``.

        Scope RBAC territorial appliqué (idem ``list_rates``).
        """
        stmt = select(TransitionRate).where(
            TransitionRate.isOutlier.is_(True),
        )
        if school_year_from_id is not None:
            stmt = stmt.where(
                TransitionRate.schoolYearFromId == school_year_from_id,
            )
        stmt = self._apply_territorial_scope(stmt, scope_user)
        stmt = stmt.order_by(
            TransitionRate.scope.asc(),
            TransitionRate.entityId.asc(),
            TransitionRate.classLevelFrom.asc(),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [TransitionRateRead.model_validate(r) for r in rows]

    # ==================================================================
    # Private helpers
    # ==================================================================
    async def _find_next_school_year(
        self, year_from_id: str,
    ) -> SchoolYear | None:
        """Renvoie la SchoolYear qui suit immédiatement ``year_from_id``.

        Critère : ``startDate`` > celle de year_from, la plus proche.
        Renvoie ``None`` si aucune (cas année courante ou dernière connue).
        """
        year_from = (
            await self.session.execute(
                select(SchoolYear).where(SchoolYear.id == year_from_id)
            )
        ).scalars().one_or_none()
        if year_from is None:
            return None
        stmt = (
            select(SchoolYear)
            .where(SchoolYear.startDate > year_from.startDate)
            .order_by(SchoolYear.startDate.asc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().one_or_none()

    async def _aggregate_by_region_level_gender(
        self, school_year_id: str,
    ) -> dict[tuple[str, EnrollmentClassLevel, Gender], int]:
        """Aggrège ``Enrollment`` filtré sur l'année + CENSUS_DECLARED.

        Renvoie un dict ``{(regionId, classLevel, gender): total}``.
        """
        stmt = (
            select(
                School.regionId,
                Enrollment.classLevel,
                Enrollment.gender,
                func.coalesce(func.sum(Enrollment.count), 0).label("total"),
            )
            .join(School, School.id == Enrollment.schoolId)
            .where(
                Enrollment.schoolYearId == school_year_id,
                Enrollment.source == EnrollmentSource.CENSUS_DECLARED,
            )
            .group_by(
                School.regionId, Enrollment.classLevel, Enrollment.gender,
            )
        )
        rows = (await self.session.execute(stmt)).all()
        out: dict[tuple[str, EnrollmentClassLevel, Gender], int] = (
            defaultdict(int)
        )
        for region_id, level, gender, total in rows:
            if region_id is None:
                continue
            out[(region_id, level, gender)] = int(total)
        return out

    def _apply_territorial_scope(self, stmt, user: User):
        """Restreint la lecture au scope territorial du user.

        Règles :
        * NATIONAL_SCOPE_ROLES → aucun filtre (tout visible).
        * REGIONAL_SCOPE_ROLES → uniquement (scope=NATIONAL) OU
          (scope=REGIONAL ET entityId=user.regionId).
        * Sinon → seulement le NATIONAL (visible publiquement).
        """
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(
                (TransitionRate.scope == TransitionScope.NATIONAL)
                | (
                    (TransitionRate.scope == TransitionScope.REGIONAL)
                    & (TransitionRate.entityId == user.regionId)
                )
            )
        # Roles sans scope régional (TEACHER, SCHOOL_DIRECTOR, …) ne
        # voient que les agrégats nationaux (information publique).
        return stmt.where(TransitionRate.scope == TransitionScope.NATIONAL)

    async def _create_outlier_anomalies(
        self,
        candidate_rows: list[TransitionRate],
    ) -> int:
        """Hook Module 9 — matérialise les outliers en AnomalyDetection.

        Idempotent : on supprime d'abord les anomalies PENDING
        ``TRANSITION_RATE_OUTLIER`` puis on rejoue le détecteur.
        Severity = MEDIUM (signal à investiguer ; pas un blocage métier).
        """
        from app.modules.anomalies.detectors import (
            detect_transition_rate_outliers,
        )
        from app.modules.anomalies.enums import (
            AnomalyStatus,
            AnomalyType,
        )
        from app.modules.anomalies.models import AnomalyDetection

        if not candidate_rows:
            return 0

        # Purge les anomalies PENDING existantes (idempotence).
        await self.session.execute(
            delete(AnomalyDetection).where(
                AnomalyDetection.type == AnomalyType.TRANSITION_RATE_OUTLIER,
                AnomalyDetection.status == AnomalyStatus.PENDING,
            )
        )

        # Le détecteur prend la liste des rates candidats en mémoire pour
        # éviter une 2e requête DB (les rates viennent d'être calculés).
        new_anomalies = await detect_transition_rate_outliers(
            self.session, outlier_rows=candidate_rows,
        )
        for a in new_anomalies:
            self.session.add(a)
        await self.session.flush()
        return len(new_anomalies)


# Rôles autorisés à déclencher une projection ou créer un scénario.
PROJECTION_WRITE_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN}
)


# ===========================================================================
# Module 2B — ProjectionService
# ===========================================================================
class ProjectionService:
    """Pilote le calcul + la lecture des projections horizon multi-années.

    L'algorithme de cohorte est isolé dans ``projection.py`` (pure). Ce
    service gère uniquement l'I/O DB (lecture des sources, écriture
    idempotente des projections) et le RBAC.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # run_projection
    # ==================================================================
    async def run_projection(
        self,
        req: RunProjectionRequest,
        actor: User,
    ) -> RunProjectionResponse:
        """Calcule + persiste les projections horizon ``horizonYears`` ans.

        1. Charge enrollments (CENSUS_DECLARED) année base par
           ``(regionId, classLevel, gender)``.
        2. Charge transition rates pour scope REGIONAL + NATIONAL
           (fallback).
        3. Itère k=1..horizonYears : applique ``project_one_year`` ;
           agrège ensuite au scope NATIONAL.
        4. Delete-then-insert dans ``ProjectedEnrollment`` filtré sur
           ``(baseSchoolYearId, scenarioId)``.

        Restreint à NATIONAL_ADMIN / MINISTRY_ADMIN.
        """
        if actor.role not in PROJECTION_WRITE_ROLES:
            raise ForbiddenError(
                detail=(
                    "Seul un administrateur central peut lancer une "
                    "projection d'effectifs."
                ),
                extra={
                    "required_any_of": sorted(
                        r.value for r in PROJECTION_WRITE_ROLES
                    )
                },
            )

        # Validation existence année base.
        base_year = (
            await self.session.execute(
                select(SchoolYear).where(SchoolYear.id == req.baseSchoolYearId)
            )
        ).scalars().one_or_none()
        if base_year is None:
            raise NotFoundError(
                detail=f"SchoolYear introuvable : {req.baseSchoolYearId}",
            )

        scenario = await self._get_scenario(req.scenarioId)
        if scenario is None:
            raise NotFoundError(
                detail=f"ProjectionScenario introuvable : {req.scenarioId}",
            )

        # Source des effectifs initiaux (CENSUS_DECLARED).
        prev_enrollments = await self._aggregate_enrollments(
            req.baseSchoolYearId,
        )
        # Rates persistés Module 2A (REGIONAL + NATIONAL).
        rates = await self._load_rates(req.baseSchoolYearId)

        # Surcharge optionnelle du scénario.
        rates = _apply_custom_rates(rates, scenario.customTransitionRates)

        growth = (
            scenario.demographicGrowthRate
            if scenario.demographicGrowthRate is not None
            else DEMOGRAPHIC_GROWTH_RATE_DEFAULT
        )

        # Wipe les projections existantes (idempotence) pour cette base
        # year + scénario.
        await self.session.execute(
            delete(ProjectedEnrollment).where(
                ProjectedEnrollment.baseSchoolYearId == req.baseSchoolYearId,
                ProjectedEnrollment.scenarioId == scenario.id,
            )
        )

        # Année de base : l'année calendrier de référence pour
        # ``projectedYear``. On utilise la year de fin (juin) pour rester
        # cohérent avec la convention IIPE "année scolaire 2024-2025 →
        # 2025".
        base_calendar_year = base_year.endDate.year
        now = datetime.now(UTC)
        regions_seen: set[str] = set()
        total_rows = 0

        for k in range(1, req.horizonYears + 1):
            projected = project_one_year(
                prev_enrollments,
                rates,
                demographic_growth=growth,
            )
            projected_year = base_calendar_year + k
            # Persistance scope REGIONAL.
            for (region_id, level, gender), count in projected.items():
                regions_seen.add(region_id)
                row = ProjectedEnrollment(
                    baseSchoolYearId=req.baseSchoolYearId,
                    projectedYear=projected_year,
                    scope=TransitionScope.REGIONAL,
                    entityId=region_id,
                    classLevel=level,
                    gender=gender,
                    projectedCount=count,
                    scenarioId=scenario.id,
                    computedAt=now,
                )
                self.session.add(row)
                total_rows += 1
            # Scope NATIONAL : somme des régions pour chaque (level, gender).
            national_totals = _aggregate_to_national(projected)
            for (level, gender), count in national_totals.items():
                row = ProjectedEnrollment(
                    baseSchoolYearId=req.baseSchoolYearId,
                    projectedYear=projected_year,
                    scope=TransitionScope.NATIONAL,
                    entityId=None,
                    classLevel=level,
                    gender=gender,
                    projectedCount=count,
                    scenarioId=scenario.id,
                    computedAt=now,
                )
                self.session.add(row)
                total_rows += 1

            # La projection de l'année t+k devient le point de départ
            # de t+k+1.
            prev_enrollments = projected

        await self.session.flush()

        return RunProjectionResponse(
            scenarioId=scenario.id,
            projectedRows=total_rows,
            regionsCovered=len(regions_seen),
            horizonYears=req.horizonYears,
            computedAt=now,
        )

    # ==================================================================
    # get_projections
    # ==================================================================
    async def get_projections(
        self,
        filters: ProjectionFilters,
        scope_user: User,
    ) -> list[ProjectedEnrollmentRead]:
        """Liste les projections persistées avec scope RBAC + pagination.

        Un REGIONAL_ADMIN ne voit que les projections de sa région
        (REGIONAL.entityId = regionId) + les NATIONAL.
        """
        stmt = select(ProjectedEnrollment)

        if filters.baseSchoolYearId is not None:
            stmt = stmt.where(
                ProjectedEnrollment.baseSchoolYearId
                == filters.baseSchoolYearId,
            )
        if filters.projectedYear is not None:
            stmt = stmt.where(
                ProjectedEnrollment.projectedYear == filters.projectedYear,
            )
        if filters.scope is not None:
            stmt = stmt.where(ProjectedEnrollment.scope == filters.scope)
        if filters.entityId is not None:
            stmt = stmt.where(ProjectedEnrollment.entityId == filters.entityId)
        if filters.classLevel is not None:
            stmt = stmt.where(
                ProjectedEnrollment.classLevel == filters.classLevel,
            )
        if filters.gender is not None:
            stmt = stmt.where(ProjectedEnrollment.gender == filters.gender)
        if filters.scenarioId is not None:
            stmt = stmt.where(
                ProjectedEnrollment.scenarioId == filters.scenarioId,
            )

        stmt = self._apply_projection_scope(stmt, scope_user)

        stmt = stmt.order_by(
            ProjectedEnrollment.projectedYear.asc(),
            ProjectedEnrollment.scope.asc(),
            ProjectedEnrollment.entityId.asc(),
            ProjectedEnrollment.classLevel.asc(),
            ProjectedEnrollment.gender.asc(),
        )
        stmt = stmt.offset(filters.offset).limit(filters.limit)

        rows = (await self.session.execute(stmt)).scalars().all()
        return [ProjectedEnrollmentRead.model_validate(r) for r in rows]

    # ==================================================================
    # Scenarios
    # ==================================================================
    async def create_scenario(
        self,
        dto: ProjectionScenarioCreate,
        actor: User,
    ) -> ProjectionScenarioRead:
        """Crée un scénario de projection (writeguard NATIONAL/MINISTRY)."""
        if actor.role not in PROJECTION_WRITE_ROLES:
            raise ForbiddenError(
                detail=(
                    "Seul un administrateur central peut créer un "
                    "scénario de projection."
                ),
                extra={
                    "required_any_of": sorted(
                        r.value for r in PROJECTION_WRITE_ROLES
                    )
                },
            )

        existing = (
            await self.session.execute(
                select(ProjectionScenario)
                .where(ProjectionScenario.name == dto.name)
            )
        ).scalars().one_or_none()
        if existing is not None:
            raise ConflictError(
                detail=f"Un scénario nommé '{dto.name}' existe déjà.",
            )

        scenario = ProjectionScenario(
            id=generate_cuid(),
            name=dto.name,
            description=dto.description,
            demographicGrowthRate=(
                dto.demographicGrowthRate
                if dto.demographicGrowthRate is not None
                else DEMOGRAPHIC_GROWTH_RATE_DEFAULT
            ),
            customTransitionRates=dto.customTransitionRates,
            createdById=actor.id,
            createdAt=datetime.now(UTC),
        )
        self.session.add(scenario)
        await self.session.flush()
        return ProjectionScenarioRead.model_validate(scenario)

    async def list_scenarios(self) -> list[ProjectionScenarioRead]:
        """Liste tous les scénarios visibles (pas de scope territorial :
        les scénarios sont nationaux par construction)."""
        rows = (
            await self.session.execute(
                select(ProjectionScenario)
                .order_by(ProjectionScenario.createdAt.desc())
            )
        ).scalars().all()
        return [ProjectionScenarioRead.model_validate(r) for r in rows]

    # ==================================================================
    # Private helpers
    # ==================================================================
    async def _get_scenario(
        self, scenario_id: str,
    ) -> ProjectionScenario | None:
        return (
            await self.session.execute(
                select(ProjectionScenario)
                .where(ProjectionScenario.id == scenario_id)
            )
        ).scalars().one_or_none()

    async def _aggregate_enrollments(
        self, school_year_id: str,
    ) -> EnrollmentMap:
        """Aggrège Enrollment (CENSUS_DECLARED) par (region, level, gender)."""
        stmt = (
            select(
                School.regionId,
                Enrollment.classLevel,
                Enrollment.gender,
                func.coalesce(func.sum(Enrollment.count), 0).label("total"),
            )
            .join(School, School.id == Enrollment.schoolId)
            .where(
                Enrollment.schoolYearId == school_year_id,
                Enrollment.source == EnrollmentSource.CENSUS_DECLARED,
            )
            .group_by(
                School.regionId, Enrollment.classLevel, Enrollment.gender,
            )
        )
        rows = (await self.session.execute(stmt)).all()
        out: EnrollmentMap = {}
        for region_id, level, gender, total in rows:
            if region_id is None:
                continue
            out[(region_id, level, gender)] = int(total)
        return out

    async def _load_rates(
        self, school_year_from_id: str,
    ) -> TransitionRateMap:
        """Charge les rates persistés Module 2A pour cette année source.

        Pour les rates ``None`` (count_from = 0 du Module 2A), on ne
        les insère pas dans le dict — l'absence signifie "aucun rate
        utilisable" et le fallback dans ``project_one_year`` s'applique.
        """
        stmt = select(TransitionRate).where(
            TransitionRate.schoolYearFromId == school_year_from_id,
        )
        rates = (await self.session.execute(stmt)).scalars().all()
        out: TransitionRateMap = {}
        for r in rates:
            if r.rate is None:
                continue
            key = (r.scope, r.entityId, r.classLevelFrom, r.gender)
            out[key] = r.rate
        return out

    def _apply_projection_scope(self, stmt, user: User):
        """Restreint la lecture au scope territorial du user."""
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(
                (ProjectedEnrollment.scope == TransitionScope.NATIONAL)
                | (
                    (ProjectedEnrollment.scope == TransitionScope.REGIONAL)
                    & (ProjectedEnrollment.entityId == user.regionId)
                )
            )
        # Roles sans scope (TEACHER, SCHOOL_DIRECTOR) : NATIONAL only.
        return stmt.where(
            ProjectedEnrollment.scope == TransitionScope.NATIONAL,
        )


def _aggregate_to_national(
    projected: EnrollmentMap,
) -> dict[tuple[EnrollmentClassLevel, Gender], int]:
    """Somme les projections régionales pour produire le NATIONAL."""
    out: dict[tuple[EnrollmentClassLevel, Gender], int] = defaultdict(int)
    for (_region_id, level, gender), count in projected.items():
        out[(level, gender)] += count
    return dict(out)


def _apply_custom_rates(
    rates: TransitionRateMap,
    custom: dict[str, Any] | None,
) -> TransitionRateMap:
    """Surcharge un sous-ensemble des rates par les overrides d'un scénario.

    Clé attendue : ``"CP1->CP2:FEMALE"`` ou ``"CP1->CP2:FEMALE:REGIONAL:<id>"``.
    Valeur : Decimal ou float (converti).

    Implémentation minimaliste — on couvre seulement le cas national
    (REGIONAL_<entityId> est plus exotique et reportable en 2B.1 si demandé).
    """
    if not custom:
        return rates
    out = dict(rates)
    for raw_key, raw_value in custom.items():
        try:
            level_pair, gender_str = raw_key.split(":", 1)
            level_from_str, _level_to_str = level_pair.split("->", 1)
            level_from = EnrollmentClassLevel(level_from_str)
            gender = Gender(gender_str)
            value = Decimal(str(raw_value))
        except (ValueError, KeyError):
            # Override mal formé : ignoré (scénario reste utilisable).
            continue
        out[(TransitionScope.NATIONAL, None, level_from, gender)] = value
    return out


# ===========================================================================
# Module 2C — CapacityDemandService
# ===========================================================================
# Rôles autorisés à déclencher un recalcul capacité (écriture). Strict :
# seuls les admins centraux. La lecture (list/critical-schools) reste
# accessible aux REGIONAL_ADMIN dans leur scope.
CAPACITY_WRITE_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN}
)


class CapacityDemandService:
    """Calcule + lit les snapshots capacité vs demande projetée (Module 2C).

    Algorithme (compute)
    --------------------
    1. Charge toutes les écoles actives (status = APPROVED) avec leur
       ``classroomsUsable``, ``regionId``, ``prefectureId``.
    2. Calcule la capacité de chaque école (``classroomsUsable × NORM``).
    3. Charge les projections REGIONAL persistées (Module 2B) pour le
       scénario : ``ProjectedEnrollment[scope=REGIONAL]``.
    4. **Redistribue** la projection régionale aux écoles au prorata de
       leur part dans les effectifs CENSUS_DECLARED de l'année de base
       (méthode IIPE simple — l'hypothèse implicite est que la
       répartition école/région reste stable sur l'horizon).
    5. Pour chaque (école × année projetée) : calcule demand, gap,
       saturationPct, severity et persiste un ``CapacityDemandSnapshot``
       (scope=SCHOOL).
    6. Agrège ensuite progressivement aux scopes PREFECTURE, REGIONAL,
       NATIONAL.

    Idempotence
    -----------
    Delete-then-insert filtré sur ``(baseSchoolYearId, scenarioId)``.
    Tout recalcul écrase l'ancien snapshot, ce qui rend l'API rejouable
    sans duplication.

    Hooks
    -----
    Module 9 anomalies — à chaque école CRITICAL sur l'horizon t+1, on
    matérialise une AnomalyDetection HIGH (``CAPACITY_CRITICAL_PROJECTED``).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # compute_capacity_demand
    # ==================================================================
    async def compute_capacity_demand(
        self,
        req: CapacityDemandRequest,
        actor: User,
    ) -> CapacityDemandResponse:
        """Calcule + persiste les snapshots capacité vs demande projetée.

        Restreint à NATIONAL_ADMIN / MINISTRY_ADMIN.
        """
        if actor.role not in CAPACITY_WRITE_ROLES:
            raise ForbiddenError(
                detail=(
                    "Seul un administrateur central peut lancer un calcul "
                    "capacité vs demande projetée."
                ),
                extra={
                    "required_any_of": sorted(
                        r.value for r in CAPACITY_WRITE_ROLES
                    )
                },
            )

        # Validation année source.
        base_year = (
            await self.session.execute(
                select(SchoolYear).where(SchoolYear.id == req.baseSchoolYearId)
            )
        ).scalars().one_or_none()
        if base_year is None:
            raise NotFoundError(
                detail=f"SchoolYear introuvable : {req.baseSchoolYearId}",
            )

        scenario = (
            await self.session.execute(
                select(ProjectionScenario)
                .where(ProjectionScenario.id == req.scenarioId)
            )
        ).scalars().one_or_none()
        if scenario is None:
            raise NotFoundError(
                detail=f"ProjectionScenario introuvable : {req.scenarioId}",
            )

        # Charge écoles actives avec capacité connue (status=APPROVED).
        # On garde les écoles à classroomsUsable=0 ou NULL pour signaler
        # le besoin (capacity=0 → severity=CRITICAL si demand > 0).
        from app.shared.enums import ValidationStatus
        schools_stmt = select(
            School.id, School.regionId, School.prefectureId,
            School.classroomsUsable,
        ).where(School.status == ValidationStatus.APPROVED)
        school_rows = (await self.session.execute(schools_stmt)).all()

        # Charge les effectifs CENSUS_DECLARED de l'année base par
        # (school, region) pour calculer la part de chaque école dans sa
        # région — sert à la redistribution de la projection régionale.
        base_enrollments_stmt = (
            select(
                Enrollment.schoolId,
                School.regionId,
                func.coalesce(func.sum(Enrollment.count), 0).label("total"),
            )
            .join(School, School.id == Enrollment.schoolId)
            .where(
                Enrollment.schoolYearId == req.baseSchoolYearId,
                Enrollment.source == EnrollmentSource.CENSUS_DECLARED,
            )
            .group_by(Enrollment.schoolId, School.regionId)
        )
        base_rows = (await self.session.execute(base_enrollments_stmt)).all()

        # base_by_school_region[(school_id, region_id)] = effectifs base
        base_by_school: dict[str, int] = {}
        base_by_region: dict[str, int] = defaultdict(int)
        for school_id, region_id, total in base_rows:
            if region_id is None:
                continue
            base_by_school[school_id] = int(total)
            base_by_region[region_id] += int(total)

        # Charge les projections REGIONAL (Module 2B) — agrégées
        # tous-niveaux/genres par (regionId, projectedYear).
        proj_stmt = (
            select(
                ProjectedEnrollment.entityId,
                ProjectedEnrollment.projectedYear,
                func.coalesce(
                    func.sum(ProjectedEnrollment.projectedCount), 0
                ).label("total"),
            )
            .where(
                ProjectedEnrollment.baseSchoolYearId == req.baseSchoolYearId,
                ProjectedEnrollment.scenarioId == scenario.id,
                ProjectedEnrollment.scope == TransitionScope.REGIONAL,
            )
            .group_by(
                ProjectedEnrollment.entityId,
                ProjectedEnrollment.projectedYear,
            )
        )
        proj_rows = (await self.session.execute(proj_stmt)).all()
        # proj_by_region_year[(region_id, year)] = total demand projetée
        proj_by_region_year: dict[tuple[str, int], int] = {}
        for region_id, year, total in proj_rows:
            if region_id is None:
                continue
            proj_by_region_year[(region_id, int(year))] = int(total)

        years_seen: set[int] = {y for (_, y) in proj_by_region_year}

        # Wipe l'ancien snapshot (idempotence) pour cette base year +
        # scénario.
        await self.session.execute(
            delete(CapacityDemandSnapshot).where(
                CapacityDemandSnapshot.baseSchoolYearId
                == req.baseSchoolYearId,
                CapacityDemandSnapshot.scenarioId == scenario.id,
            )
        )

        now = datetime.now(UTC)

        # ----------------------------------------------------------------
        # 1) Scope SCHOOL : redistribution proportionnelle + persistance
        # ----------------------------------------------------------------
        # Accumulateurs pour agrégation up-stream.
        # cap_by_pref[prefId][year] = (capacity, demand)
        cap_by_pref: dict[str, dict[int, tuple[int, int]]] = defaultdict(
            lambda: defaultdict(lambda: (0, 0)),
        )
        cap_by_region: dict[str, dict[int, tuple[int, int]]] = defaultdict(
            lambda: defaultdict(lambda: (0, 0)),
        )
        cap_national: dict[int, tuple[int, int]] = defaultdict(
            lambda: (0, 0),
        )

        total_schools_analyzed = 0
        total_critical = 0
        total_warning = 0
        rows_persisted = 0
        critical_school_rows: list[CapacityDemandSnapshot] = []

        for school_id, region_id, prefecture_id, classrooms_usable in school_rows:
            total_schools_analyzed += 1
            usable = int(classrooms_usable or 0)
            capacity = compute_school_capacity(
                usable, STUDENTS_PER_CLASSROOM_NORM,
            )
            school_base = base_by_school.get(school_id, 0)
            region_base = base_by_region.get(region_id, 0)
            # Part de l'école dans sa région ; si la région a 0 effectif
            # base déclaré, on retombe sur 1/N (N=écoles de la région) —
            # cas démarrage à froid (pas de recensement encore).
            schools_in_region = sum(
                1 for sid, rid, _pid, _u in school_rows if rid == region_id
            )
            for year in sorted(years_seen):
                region_demand = proj_by_region_year.get(
                    (region_id, year), 0,
                )
                if region_demand <= 0:
                    school_demand = 0
                elif region_base > 0:
                    # Part proportionnelle (méthode IIPE simple).
                    school_demand = round(
                        region_demand * school_base / region_base
                    )
                elif schools_in_region > 0:
                    # Pas de base déclarée → on étale uniformément.
                    school_demand = region_demand // schools_in_region
                else:
                    school_demand = 0

                gap = compute_gap(school_demand, capacity)
                saturation = compute_saturation_pct(school_demand, capacity)
                severity = compute_severity(saturation)

                # On ne persiste pas les rows demand=0 ET capacity=0
                # (école sans capacité ni demande projetée — pas
                # actionable, juste du bruit).
                if school_demand == 0 and capacity == 0:
                    continue

                row = CapacityDemandSnapshot(
                    baseSchoolYearId=req.baseSchoolYearId,
                    projectedYear=year,
                    scope=CapacityScope.SCHOOL,
                    entityId=school_id,
                    capacity=capacity,
                    demand=school_demand,
                    gap=gap,
                    saturationPct=saturation,
                    severity=severity,
                    scenarioId=scenario.id,
                    computedAt=now,
                )
                self.session.add(row)
                rows_persisted += 1
                if severity == CapacitySeverity.CRITICAL:
                    total_critical += 1
                    critical_school_rows.append(row)
                elif severity == CapacitySeverity.WARNING:
                    total_warning += 1

                # Accumule pour les scopes supérieurs.
                if prefecture_id is not None:
                    prev_cap, prev_dem = cap_by_pref[prefecture_id][year]
                    cap_by_pref[prefecture_id][year] = (
                        prev_cap + capacity, prev_dem + school_demand,
                    )
                if region_id is not None:
                    prev_cap, prev_dem = cap_by_region[region_id][year]
                    cap_by_region[region_id][year] = (
                        prev_cap + capacity, prev_dem + school_demand,
                    )
                prev_cap, prev_dem = cap_national[year]
                cap_national[year] = (
                    prev_cap + capacity, prev_dem + school_demand,
                )

        # ----------------------------------------------------------------
        # 2) Scopes PREFECTURE / REGIONAL / NATIONAL
        # ----------------------------------------------------------------
        def _persist_aggregate(
            scope: CapacityScope, entity_id: str | None, year: int,
            capacity: int, demand: int,
        ) -> None:
            nonlocal rows_persisted
            gap = compute_gap(demand, capacity)
            saturation = compute_saturation_pct(demand, capacity)
            severity = compute_severity(saturation)
            self.session.add(CapacityDemandSnapshot(
                baseSchoolYearId=req.baseSchoolYearId,
                projectedYear=year,
                scope=scope,
                entityId=entity_id,
                capacity=capacity,
                demand=demand,
                gap=gap,
                saturationPct=saturation,
                severity=severity,
                scenarioId=scenario.id,
                computedAt=now,
            ))
            rows_persisted += 1

        for pref_id, by_year in cap_by_pref.items():
            for year, (capacity, demand) in by_year.items():
                _persist_aggregate(
                    CapacityScope.PREFECTURE, pref_id, year, capacity, demand,
                )

        for region_id, by_year in cap_by_region.items():
            for year, (capacity, demand) in by_year.items():
                _persist_aggregate(
                    CapacityScope.REGIONAL, region_id, year, capacity, demand,
                )

        for year, (capacity, demand) in cap_national.items():
            _persist_aggregate(
                CapacityScope.NATIONAL, None, year, capacity, demand,
            )

        await self.session.flush()

        # Hook Module 9 — anomalies pour les écoles CRITICAL.
        await self._create_capacity_anomalies(critical_school_rows)

        return CapacityDemandResponse(
            scenarioId=scenario.id,
            totalSchoolsAnalyzed=total_schools_analyzed,
            totalCritical=total_critical,
            totalWarning=total_warning,
            rowsPersisted=rows_persisted,
            computedAt=now,
        )

    # ==================================================================
    # Lecture
    # ==================================================================
    async def list_capacity_demand(
        self,
        filters: CapacityDemandFilters,
        scope_user: User,
    ) -> list[CapacityDemandRow]:
        """Liste les snapshots avec filtrage + scope RBAC territorial."""
        stmt = select(CapacityDemandSnapshot)

        if filters.baseSchoolYearId is not None:
            stmt = stmt.where(
                CapacityDemandSnapshot.baseSchoolYearId
                == filters.baseSchoolYearId,
            )
        if filters.projectedYear is not None:
            stmt = stmt.where(
                CapacityDemandSnapshot.projectedYear == filters.projectedYear,
            )
        if filters.scope is not None:
            stmt = stmt.where(CapacityDemandSnapshot.scope == filters.scope)
        if filters.entityId is not None:
            stmt = stmt.where(
                CapacityDemandSnapshot.entityId == filters.entityId,
            )
        if filters.severity is not None:
            stmt = stmt.where(
                CapacityDemandSnapshot.severity == filters.severity,
            )
        if filters.scenarioId is not None:
            stmt = stmt.where(
                CapacityDemandSnapshot.scenarioId == filters.scenarioId,
            )

        stmt = self._apply_capacity_scope(stmt, scope_user)
        stmt = stmt.order_by(
            CapacityDemandSnapshot.projectedYear.asc(),
            CapacityDemandSnapshot.scope.asc(),
            CapacityDemandSnapshot.severity.desc(),
            CapacityDemandSnapshot.entityId.asc(),
        )
        stmt = stmt.offset(filters.offset).limit(filters.limit)

        rows = (await self.session.execute(stmt)).scalars().all()
        return [CapacityDemandRow.model_validate(r) for r in rows]

    async def list_critical_schools_for_investment(
        self,
        scope_user: User,
        *,
        limit: int = 50,
        base_school_year_id: str | None = None,
    ) -> list[CapacityDemandRow]:
        """Top N écoles CRITICAL — input direct pour Module 3C investissement.

        On filtre sur ``scope=SCHOOL`` + ``severity=CRITICAL`` et on trie par
        ``gap`` décroissant (les écoles avec le plus grand déficit en
        premier). Le scope RBAC reste appliqué.
        """
        stmt = select(CapacityDemandSnapshot).where(
            CapacityDemandSnapshot.scope == CapacityScope.SCHOOL,
            CapacityDemandSnapshot.severity == CapacitySeverity.CRITICAL,
        )
        if base_school_year_id is not None:
            stmt = stmt.where(
                CapacityDemandSnapshot.baseSchoolYearId
                == base_school_year_id,
            )
        stmt = self._apply_capacity_scope(stmt, scope_user)
        stmt = stmt.order_by(
            CapacityDemandSnapshot.gap.desc(),
        ).limit(max(1, min(limit, 500)))

        rows = (await self.session.execute(stmt)).scalars().all()
        return [CapacityDemandRow.model_validate(r) for r in rows]

    # ==================================================================
    # Private helpers
    # ==================================================================
    def _apply_capacity_scope(self, stmt, user: User):
        """Restreint la lecture au scope territorial du user.

        * NATIONAL_SCOPE_ROLES → tout visible.
        * REGIONAL_SCOPE_ROLES → NATIONAL OU (REGIONAL/PREFECTURE/SCHOOL de
          la région du user). Pour PREFECTURE/SCHOOL on filtre via la
          School/Prefecture appartenant à la région (sous-requête).
        * Sinon → seulement les agrégats NATIONAL.
        """
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            # Sous-requête : écoles + préfectures de la région du user.
            schools_in_region = select(School.id).where(
                School.regionId == user.regionId,
            )
            from app.modules.territory.models import Prefecture
            prefs_in_region = select(Prefecture.id).where(
                Prefecture.regionId == user.regionId,
            )
            return stmt.where(
                (CapacityDemandSnapshot.scope == CapacityScope.NATIONAL)
                | (
                    (CapacityDemandSnapshot.scope == CapacityScope.REGIONAL)
                    & (CapacityDemandSnapshot.entityId == user.regionId)
                )
                | (
                    (CapacityDemandSnapshot.scope == CapacityScope.PREFECTURE)
                    & (CapacityDemandSnapshot.entityId.in_(prefs_in_region))
                )
                | (
                    (CapacityDemandSnapshot.scope == CapacityScope.SCHOOL)
                    & (CapacityDemandSnapshot.entityId.in_(schools_in_region))
                )
            )
        # Sinon : visibilité limitée aux agrégats nationaux (information
        # publique).
        return stmt.where(
            CapacityDemandSnapshot.scope == CapacityScope.NATIONAL,
        )

    async def _create_capacity_anomalies(
        self,
        critical_rows: list[CapacityDemandSnapshot],
    ) -> int:
        """Hook Module 9 — matérialise les écoles CRITICAL en AnomalyDetection.

        Idempotent : on supprime d'abord les anomalies PENDING
        ``CAPACITY_CRITICAL_PROJECTED`` puis on rejoue le détecteur.
        Severity = HIGH (planification infrastructure prioritaire).

        On se limite aux écoles CRITICAL sur l'horizon **t+1** : les
        années plus lointaines ont une incertitude croissante et le
        cabinet ne veut pas être noyé par des alertes 5 ans à l'avance.
        """
        from app.modules.anomalies.detectors import detect_capacity_critical
        from app.modules.anomalies.enums import AnomalyStatus, AnomalyType
        from app.modules.anomalies.models import AnomalyDetection

        if not critical_rows:
            return 0

        # Ne garde que les rows de l'horizon t+1 (année la plus proche).
        min_year = min(r.projectedYear for r in critical_rows)
        nearest = [r for r in critical_rows if r.projectedYear == min_year]
        if not nearest:
            return 0

        # Purge des anomalies PENDING existantes (idempotence).
        await self.session.execute(
            delete(AnomalyDetection).where(
                AnomalyDetection.type == AnomalyType.CAPACITY_CRITICAL_PROJECTED,
                AnomalyDetection.status == AnomalyStatus.PENDING,
            )
        )

        new_anomalies = await detect_capacity_critical(
            self.session, critical_school_rows=nearest,
        )
        for a in new_anomalies:
            self.session.add(a)
        await self.session.flush()
        return len(new_anomalies)


__all__ = [
    "CAPACITY_WRITE_ROLES",
    "COMPUTE_TRANSITIONS_ROLES",
    "PROJECTION_WRITE_ROLES",
    "CapacityDemandService",
    "ProjectionService",
    "TransitionRateService",
]
