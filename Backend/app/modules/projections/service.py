"""Module 2A — Service TransitionRate.

Encapsule la logique métier des taux de transition :

* ``compute_transitions`` : recalcul + persistance des rates pour une
  liste d'années sources. Idempotent (upsert via unique).
* ``list_rates`` : lecture filtrée + scope RBAC territorial.
* ``get_outliers`` : lecture filtrée sur les rates flaggés ``isOutlier``.

Toutes les méthodes sont ``async``. Le calcul s'appuie sur les
agrégats ``Enrollment`` (source = ``CENSUS_DECLARED``).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ForbiddenError,
    NotFoundError,
)
from app.modules.academics.models import SchoolYear
from app.modules.auth.models import User
from app.modules.enrollment.enums import EnrollmentClassLevel, EnrollmentSource
from app.modules.enrollment.models import Enrollment
from app.modules.projections.enums import TransitionScope
from app.modules.projections.models import TransitionRate
from app.modules.projections.schemas import (
    ComputeTransitionsResponse,
    TransitionRateFilters,
    TransitionRateRead,
)
from app.modules.projections.transitions import (
    LEVEL_PAIRS,
    compute_rate,
)
from app.modules.schools.models import School
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


__all__ = [
    "COMPUTE_TRANSITIONS_ROLES",
    "TransitionRateService",
]


# Référence implicite : ``Decimal`` est utilisé via les rates SQLAlchemy ;
# on garde l'import sous silence pour le linter au cas où.
_ = Decimal
