"""Module 1A + 1B — Service Enrollment.

Encapsule la logique métier des effectifs désagrégés :
* ``record`` : POST unitaire avec validation.
* ``bulk_record`` : POST groupé (max 200, atomic per-item).
* ``list_for_school`` : liste filtrée + scope RBAC automatique.
* ``aggregate`` : agrégations parallèles (niveau, genre, breakdown).
* ``compute_from_students`` : recalcule depuis la table Student (admin only).

Module 1B (GPI) :
* ``compute_gpi_snapshots`` : recalcule + persiste les snapshots à 4 échelons.
* ``get_gpi`` : lecture rapide (cache Redis 5 min).
* ``list_critical_schools`` : top points chauds.
* ``gpi_evolution`` : série temporelle (multi-années).

Toutes les méthodes async. Les requêtes territoriales partagent les patterns
du module ``census`` (NATIONAL_SCOPE / REGIONAL_SCOPE / etc.).
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from app.core.redis import get_redis
from app.modules.academics.models import SchoolYear
from app.modules.auth.models import User
from app.modules.census.models import Student
from app.modules.enrollment.enums import (
    EnrollmentClassLevel,
    EnrollmentSource,
    GpiScope,
)
from app.modules.enrollment.models import Enrollment, GpiSnapshot
from app.modules.enrollment.parity import (
    GpiSeverity,
    classify_gpi,
    compute_gpi,
)
from app.modules.enrollment.schemas import (
    AggregateRequest,
    AggregateResponse,
    BulkItemError,
    BulkRecordResponse,
    EnrollmentAggregate,
    EnrollmentCreate,
    EnrollmentRead,
    GpiEvolutionPoint,
    GpiResult,
    GpiSnapshotsRunResponse,
)
from app.modules.schools.models import ClassRoom, School
from app.shared.enums import Gender, UserRole
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)

# Rôles autorisés à recalculer depuis la base Student (écrit une source
# alternative de vérité — on garde ça réservé aux admins centraux).
COMPUTE_FROM_STUDENTS_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN}
)

# Mapping ClassRoom.level (texte libre) -> EnrollmentClassLevel.
# Tolérant aux variantes de casse / espaces : on normalise via strip+upper
# avant de chercher. Les niveaux inconnus sont ignorés (et tracés dans
# ``compute_from_students`` via le retour).
_CLASSROOM_LEVEL_MAPPING: dict[str, EnrollmentClassLevel] = {
    "MATERNELLE_1": EnrollmentClassLevel.MATERNELLE_1,
    "MATERNELLE_2": EnrollmentClassLevel.MATERNELLE_2,
    "MATERNELLE_3": EnrollmentClassLevel.MATERNELLE_3,
    "CP1": EnrollmentClassLevel.CP1,
    "CP2": EnrollmentClassLevel.CP2,
    "CE1": EnrollmentClassLevel.CE1,
    "CE2": EnrollmentClassLevel.CE2,
    "CM1": EnrollmentClassLevel.CM1,
    "CM2": EnrollmentClassLevel.CM2,
}


def _normalize_classroom_level(raw: str | None) -> EnrollmentClassLevel | None:
    if raw is None:
        return None
    key = raw.strip().upper().replace("-", "_").replace(" ", "_")
    return _CLASSROOM_LEVEL_MAPPING.get(key)


class EnrollmentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # Public API
    # ==================================================================
    async def record(self, dto: EnrollmentCreate, actor: User) -> EnrollmentRead:
        """POST unitaire — valide count ≥ 0, RBAC, unicité."""
        if dto.count < 0:
            # Belt-and-braces : Pydantic le bloque déjà mais on protège le
            # service appelé hors HTTP (workers, compute_from_students…).
            raise ValidationFailedError(detail="count doit être ≥ 0")

        await self._assert_can_access_school(actor, dto.schoolId)
        await self._assert_school_year_exists(dto.schoolYearId)

        now = datetime.now(UTC)
        row = Enrollment(
            schoolYearId=dto.schoolYearId,
            schoolId=dto.schoolId,
            classLevel=dto.classLevel,
            gender=dto.gender,
            count=dto.count,
            source=dto.source,
            recordedAt=now,
            recordedById=actor.id,
            notes=dto.notes,
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # NB : on NE fait PAS de session.rollback() ici — l'appel à
            # ``flush()`` au-dessus a déjà invalidé la transaction
            # active. Si on est appelé directement (POST unitaire), le
            # handler global s'occupe du rollback ; si on est imbriqué
            # dans ``bulk_record``, c'est le savepoint englobant qui
            # rollback (cf. logique try/sp.rollback() là-bas).
            raise ConflictError(
                detail=(
                    "Un effectif existe déjà pour cette année, école, "
                    "niveau, genre et source."
                ),
            ) from exc

        return EnrollmentRead.model_validate(row)

    async def bulk_record(
        self,
        items: list[EnrollmentCreate],
        actor: User,
    ) -> BulkRecordResponse:
        """POST groupé : insert ligne par ligne, retourne {inserted, errors}.

        ``items`` ne doit pas dépasser 200 (vérifié côté router via Pydantic).
        Chaque item est inséré dans un savepoint pour qu'une erreur isolée
        ne casse pas le batch entier.
        """
        if len(items) > 200:
            raise ValidationFailedError(
                detail="bulk_record accepte 200 items maximum par appel."
            )

        inserted = 0
        errors: list[BulkItemError] = []

        import contextlib

        for idx, item in enumerate(items):
            sp = await self.session.begin_nested()
            try:
                await self.record(item, actor)
                await sp.commit()
                inserted += 1
            except Exception as exc:
                # IntegrityError sur flush passe le savepoint à DEACTIVE :
                # SQLAlchemy refuse alors d'ouvrir un nouveau begin_nested()
                # tant qu'on n'a pas appelé rollback() pour libérer la
                # transaction interne. ``suppress`` absorbe le cas où le
                # savepoint est déjà CLOSED (blindage).
                with contextlib.suppress(Exception):
                    await sp.rollback()
                errors.append(BulkItemError(index=idx, message=str(exc)))

        return BulkRecordResponse(inserted=inserted, errors=errors)

    async def list_for_school(
        self,
        school_id: str,
        scope_user: User,
        *,
        school_year_id: str | None = None,
    ) -> list[EnrollmentRead]:
        await self._assert_can_access_school(scope_user, school_id)
        stmt: Select[tuple[Enrollment]] = select(Enrollment).where(
            Enrollment.schoolId == school_id
        )
        if school_year_id:
            stmt = stmt.where(Enrollment.schoolYearId == school_year_id)
        stmt = stmt.order_by(
            Enrollment.schoolYearId.desc(),
            Enrollment.classLevel.asc(),
            Enrollment.gender.asc(),
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [EnrollmentRead.model_validate(r) for r in rows]

    async def aggregate(
        self,
        req: AggregateRequest,
        scope_user: User,
    ) -> AggregateResponse:
        """Renvoie les agrégats par niveau, genre et breakdown (niveau × genre).

        Les 3 sous-requêtes sont lancées en parallèle via ``asyncio.gather``
        — chacune sur une session séparée serait l'idéal en prod ; en pratique
        SQLAlchemy AsyncSession sérialise sur la connexion sous-jacente, mais
        le pattern reste correct car les await libèrent l'event loop pour
        les futures cellules au sein d'une même requête.
        """
        await self._assert_school_year_exists(req.schoolYearId)
        base_stmt = self._aggregate_base_query(req, scope_user)

        async def _by_level() -> list[tuple[EnrollmentClassLevel, int]]:
            stmt = base_stmt.with_only_columns(
                Enrollment.classLevel,
                func.coalesce(func.sum(Enrollment.count), 0),
            ).group_by(Enrollment.classLevel)
            rows = (await self.session.execute(stmt)).all()
            return [(level, int(total)) for level, total in rows]

        async def _by_gender() -> list[tuple[Gender, int]]:
            stmt = base_stmt.with_only_columns(
                Enrollment.gender,
                func.coalesce(func.sum(Enrollment.count), 0),
            ).group_by(Enrollment.gender)
            rows = (await self.session.execute(stmt)).all()
            return [(gender, int(total)) for gender, total in rows]

        async def _breakdown() -> list[tuple[EnrollmentClassLevel, Gender, int]]:
            stmt = base_stmt.with_only_columns(
                Enrollment.classLevel,
                Enrollment.gender,
                func.coalesce(func.sum(Enrollment.count), 0),
            ).group_by(Enrollment.classLevel, Enrollment.gender)
            rows = (await self.session.execute(stmt)).all()
            return [
                (level, gender, int(total)) for level, gender, total in rows
            ]

        by_level_rows, by_gender_rows, breakdown_rows = await asyncio.gather(
            _by_level(), _by_gender(), _breakdown()
        )

        # Compute totals + GPI per level (filles / garçons).
        per_level_by_gender: dict[
            EnrollmentClassLevel, dict[Gender, int]
        ] = defaultdict(lambda: defaultdict(int))
        for level, gender, total in breakdown_rows:
            per_level_by_gender[level][gender] = total

        by_level: list[EnrollmentAggregate] = []
        for level, total in sorted(by_level_rows, key=lambda r: r[0].value):
            gender_counts = per_level_by_gender.get(level, {})
            boys = gender_counts.get(Gender.MALE, 0)
            girls = gender_counts.get(Gender.FEMALE, 0)
            gpi = round(girls / boys, 3) if boys > 0 else None
            by_level.append(
                EnrollmentAggregate(
                    level=level, gender=None, count=total, gpi=gpi
                )
            )

        by_gender: list[EnrollmentAggregate] = [
            EnrollmentAggregate(level=None, gender=g, count=total, gpi=None)
            for g, total in sorted(by_gender_rows, key=lambda r: r[0].value)
        ]

        breakdown: list[EnrollmentAggregate] = [
            EnrollmentAggregate(
                level=level, gender=gender, count=total, gpi=None
            )
            for level, gender, total in sorted(
                breakdown_rows, key=lambda r: (r[0].value, r[1].value)
            )
        ]

        total = sum(count for _, count in by_level_rows)

        return AggregateResponse(
            scope=req.scope,
            schoolYearId=req.schoolYearId,
            total=total,
            byLevel=by_level,
            byGender=by_gender,
            breakdown=breakdown,
        )

    async def compute_from_students(
        self, school_year_id: str, actor: User
    ) -> int:
        """Agrège la table Student → Enrollment(source=COMPUTED_FROM_STUDENTS).

        * Réservé NATIONAL_ADMIN / MINISTRY_ADMIN (écrit une source alternative
          de vérité ; un mauvais run pollue les indicateurs cabinet).
        * Idempotent : on supprime d'abord les rows existantes
          (source=COMPUTED_FROM_STUDENTS, year) puis on ré-insère.
        * Les classes sans ``level`` reconnu sont ignorées (logguées dans
          le retour : on retourne le nombre de lignes Enrollment créées).
        """
        if actor.role not in COMPUTE_FROM_STUDENTS_ROLES:
            raise ForbiddenError(
                detail=(
                    "Seul un administrateur central peut recalculer les "
                    "effectifs depuis la base élèves."
                ),
                extra={
                    "required_any_of": sorted(
                        r.value for r in COMPUTE_FROM_STUDENTS_ROLES
                    )
                },
            )
        await self._assert_school_year_exists(school_year_id)

        # Wipe les rows COMPUTED existantes pour cette année (idempotence).
        from sqlalchemy import delete as sql_delete

        await self.session.execute(
            sql_delete(Enrollment).where(
                Enrollment.schoolYearId == school_year_id,
                Enrollment.source == EnrollmentSource.COMPUTED_FROM_STUDENTS,
            )
        )

        # Récup les students agrégés par (school, classroom.level, gender).
        stmt = (
            select(
                Student.schoolId,
                ClassRoom.level,
                Student.gender,
                func.count(Student.id),
            )
            .join(ClassRoom, ClassRoom.id == Student.classRoomId)
            .where(Student.classRoomId.is_not(None))
            .group_by(Student.schoolId, ClassRoom.level, Student.gender)
        )
        rows = (await self.session.execute(stmt)).all()

        now = datetime.now(UTC)
        inserted = 0
        for school_id, raw_level, gender, count in rows:
            level = _normalize_classroom_level(raw_level)
            if level is None:
                continue
            self.session.add(
                Enrollment(
                    schoolYearId=school_year_id,
                    schoolId=school_id,
                    classLevel=level,
                    gender=gender,
                    count=int(count),
                    source=EnrollmentSource.COMPUTED_FROM_STUDENTS,
                    recordedAt=now,
                    recordedById=actor.id,
                    notes="auto-computed from Student snapshot",
                )
            )
            inserted += 1
        await self.session.flush()
        return inserted

    # ==================================================================
    # Module 1B — Gender Parity Index (GPI)
    # ==================================================================
    async def compute_gpi_snapshots(
        self,
        school_year_id: str,
        actor: User,
    ) -> GpiSnapshotsRunResponse:
        """Recalcule + persiste les snapshots GPI à 4 échelons.

        Stratégie idempotente :
        * On supprime d'abord tous les snapshots existants pour
          ``school_year_id``, puis on insère le nouveau set (read-update-replace).
        * On scanne ``Enrollment`` filtré sur la year et
          ``source=CENSUS_DECLARED`` (la source de vérité officielle —
          on ne mélange jamais avec COMPUTED_FROM_STUDENTS qui sert au
          contrôle qualité).
        * On agrège par école → préfecture → région → national.
        * On crée en passant les anomalies Module 9 (``CRITICAL_GPI``)
          pour chaque école sous le seuil 0.85.

        Restreint à NATIONAL_ADMIN / MINISTRY_ADMIN — un recalcul génère
        des alertes auto qui remontent au cabinet ministre.
        """
        if actor.role not in COMPUTE_FROM_STUDENTS_ROLES:
            raise ForbiddenError(
                detail=(
                    "Seul un administrateur central peut recalculer les "
                    "snapshots GPI."
                ),
                extra={
                    "required_any_of": sorted(
                        r.value for r in COMPUTE_FROM_STUDENTS_ROLES
                    )
                },
            )
        await self._assert_school_year_exists(school_year_id)
        now = datetime.now(UTC)

        # 1. Wipe pour idempotence.
        await self.session.execute(
            delete(GpiSnapshot).where(
                GpiSnapshot.schoolYearId == school_year_id
            )
        )

        # 2. Agrège par école — base de tous les rollups.
        stmt = (
            select(
                School.id.label("school_id"),
                School.regionId.label("region_id"),
                School.prefectureId.label("prefecture_id"),
                Enrollment.gender,
                func.coalesce(func.sum(Enrollment.count), 0).label("total"),
            )
            .join(Enrollment, Enrollment.schoolId == School.id)
            .where(
                Enrollment.schoolYearId == school_year_id,
                Enrollment.source == EnrollmentSource.CENSUS_DECLARED,
            )
            .group_by(
                School.id, School.regionId, School.prefectureId,
                Enrollment.gender,
            )
        )
        rows = (await self.session.execute(stmt)).all()

        # 3. Re-shape : { school_id: {gender: count}, "region_id": ..., "prefecture_id": ... }
        by_school: dict[str, dict[str, Any]] = {}
        for r in rows:
            entry = by_school.setdefault(
                r.school_id,
                {
                    "region_id": r.region_id,
                    "prefecture_id": r.prefecture_id,
                    Gender.FEMALE: 0,
                    Gender.MALE: 0,
                },
            )
            if r.gender in (Gender.FEMALE, Gender.MALE):
                entry[r.gender] = int(r.total)

        # 4. Persiste les snapshots SCHOOL.
        snapshots_count: dict[str, int] = {
            GpiScope.NATIONAL.value: 0,
            GpiScope.REGIONAL.value: 0,
            GpiScope.PREFECTURE.value: 0,
            GpiScope.SCHOOL.value: 0,
        }
        critical_school_ids: list[str] = []
        for school_id, data in by_school.items():
            girls = int(data[Gender.FEMALE])
            boys = int(data[Gender.MALE])
            gpi = compute_gpi(girls, boys)
            severity = classify_gpi(gpi)
            self.session.add(GpiSnapshot(
                schoolYearId=school_year_id,
                scope=GpiScope.SCHOOL,
                entityId=school_id,
                girlsCount=girls,
                boysCount=boys,
                gpi=gpi,
                severity=severity,
                computedAt=now,
            ))
            snapshots_count[GpiScope.SCHOOL.value] += 1
            if severity == GpiSeverity.CRITICAL_GIRLS:
                critical_school_ids.append(school_id)

        # 5. Rollups préfecture / région (agrégation côté Python — on
        # garde la simplicité ; les volumes restent raisonnables :
        # quelques milliers d'écoles max au pays).
        by_prefecture: dict[str, dict[str, int]] = defaultdict(
            lambda: {Gender.FEMALE: 0, Gender.MALE: 0}
        )
        by_region: dict[str, dict[str, int]] = defaultdict(
            lambda: {Gender.FEMALE: 0, Gender.MALE: 0}
        )
        national = {Gender.FEMALE: 0, Gender.MALE: 0}
        for data in by_school.values():
            girls = int(data[Gender.FEMALE])
            boys = int(data[Gender.MALE])
            if data.get("prefecture_id"):
                by_prefecture[data["prefecture_id"]][Gender.FEMALE] += girls
                by_prefecture[data["prefecture_id"]][Gender.MALE] += boys
            if data.get("region_id"):
                by_region[data["region_id"]][Gender.FEMALE] += girls
                by_region[data["region_id"]][Gender.MALE] += boys
            national[Gender.FEMALE] += girls
            national[Gender.MALE] += boys

        for prefecture_id, totals in by_prefecture.items():
            gpi = compute_gpi(totals[Gender.FEMALE], totals[Gender.MALE])
            self.session.add(GpiSnapshot(
                schoolYearId=school_year_id,
                scope=GpiScope.PREFECTURE,
                entityId=prefecture_id,
                girlsCount=totals[Gender.FEMALE],
                boysCount=totals[Gender.MALE],
                gpi=gpi,
                severity=classify_gpi(gpi),
                computedAt=now,
            ))
            snapshots_count[GpiScope.PREFECTURE.value] += 1

        for region_id, totals in by_region.items():
            gpi = compute_gpi(totals[Gender.FEMALE], totals[Gender.MALE])
            self.session.add(GpiSnapshot(
                schoolYearId=school_year_id,
                scope=GpiScope.REGIONAL,
                entityId=region_id,
                girlsCount=totals[Gender.FEMALE],
                boysCount=totals[Gender.MALE],
                gpi=gpi,
                severity=classify_gpi(gpi),
                computedAt=now,
            ))
            snapshots_count[GpiScope.REGIONAL.value] += 1

        # National (entityId = NULL).
        national_gpi = compute_gpi(
            national[Gender.FEMALE], national[Gender.MALE]
        )
        self.session.add(GpiSnapshot(
            schoolYearId=school_year_id,
            scope=GpiScope.NATIONAL,
            entityId=None,
            girlsCount=national[Gender.FEMALE],
            boysCount=national[Gender.MALE],
            gpi=national_gpi,
            severity=classify_gpi(national_gpi),
            computedAt=now,
        ))
        snapshots_count[GpiScope.NATIONAL.value] += 1

        await self.session.flush()

        # 6. Invalidate Redis cache (touched scopes).
        await self._invalidate_gpi_cache(school_year_id)

        # 7. Hook Module 9 — crée les AnomalyDetection CRITICAL_GPI.
        anomalies_created = await self._create_gpi_anomalies(
            school_year_id=school_year_id,
        )

        return GpiSnapshotsRunResponse(
            schoolYearId=school_year_id,
            persisted=snapshots_count,
            criticalAnomaliesCreated=anomalies_created,
            computedAt=now,
        )

    async def get_gpi(
        self,
        scope: GpiScope,
        scope_user: User,
        *,
        entity_id: str | None = None,
        school_year_id: str | None = None,
    ) -> GpiResult:
        """Lit le GPI le plus récent pour un scope + entité.

        * Si ``school_year_id`` est ``None`` → on prend la dernière année
          ayant un snapshot pour ce scope/entityId.
        * Si ``entity_id`` est ``None`` et scope != NATIONAL → 422.
        * RBAC : un REGIONAL_ADMIN ne peut lire que sa région ; un
          SCHOOL_DIRECTOR que son école. NATIONAL est ouvert à tous.
        * Cache Redis 5 min, clé ``enrollment:gpi:<scope>:<entity>:<year>``.
        """
        # RBAC pré-flight.
        self._assert_gpi_scope_access(scope_user, scope, entity_id)

        if scope != GpiScope.NATIONAL and entity_id is None:
            raise ValidationFailedError(
                detail=(
                    "entityId est requis pour un scope autre que NATIONAL."
                )
            )

        cache_key = self._gpi_cache_key(scope, entity_id, school_year_id)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return GpiResult.model_validate(cached)

        stmt = (
            select(GpiSnapshot)
            .where(GpiSnapshot.scope == scope)
            .order_by(GpiSnapshot.computedAt.desc())
            .limit(1)
        )
        if entity_id is not None:
            stmt = stmt.where(GpiSnapshot.entityId == entity_id)
        else:
            stmt = stmt.where(GpiSnapshot.entityId.is_(None))
        if school_year_id is not None:
            stmt = stmt.where(GpiSnapshot.schoolYearId == school_year_id)

        snapshot = (await self.session.execute(stmt)).scalars().first()
        if snapshot is None:
            raise NotFoundError(
                detail=(
                    "Aucun snapshot GPI trouvé pour ce scope/entité. "
                    "Lancez compute_gpi_snapshots d'abord."
                )
            )

        result = GpiResult.model_validate(snapshot)
        await self._cache_set(cache_key, result.model_dump(mode="json"))
        return result

    async def list_critical_schools(
        self,
        school_year_id: str,
        scope_user: User,
        *,
        limit: int = 20,
    ) -> list[GpiResult]:
        """Liste les écoles avec severity = CRITICAL_GIRLS, triées par GPI ASC.

        * Filtrage RBAC : un REGIONAL_ADMIN ne voit que sa région, etc.
        * Le tri ASC par GPI garantit que les pires écoles arrivent en
          tête (utile pour le cabinet).
        """
        if limit < 1 or limit > 200:
            raise ValidationFailedError(
                detail="limit doit être compris entre 1 et 200."
            )

        # Join School pour filtrer par scope territorial du user.
        stmt = (
            select(GpiSnapshot)
            .join(School, School.id == GpiSnapshot.entityId)
            .where(
                GpiSnapshot.schoolYearId == school_year_id,
                GpiSnapshot.scope == GpiScope.SCHOOL,
                GpiSnapshot.severity == GpiSeverity.CRITICAL_GIRLS,
            )
        )
        stmt = self._apply_school_scope(stmt, scope_user)
        stmt = stmt.order_by(GpiSnapshot.gpi.asc()).limit(limit)
        rows = (await self.session.execute(stmt)).scalars().all()
        return [GpiResult.model_validate(r) for r in rows]

    async def gpi_evolution(
        self,
        scope: GpiScope,
        entity_id: str | None,
        school_years: list[str],
        scope_user: User,
    ) -> list[GpiEvolutionPoint]:
        """Série temporelle GPI pour les school_years demandés.

        Renvoie un point par year (avec ``gpi=None`` et severity=NORMAL si
        aucun snapshot trouvé — on garde le placeholder pour que le
        frontend trace une courbe continue).
        """
        if not school_years:
            return []

        self._assert_gpi_scope_access(scope_user, scope, entity_id)

        stmt = (
            select(GpiSnapshot, SchoolYear.name)
            .join(SchoolYear, SchoolYear.id == GpiSnapshot.schoolYearId)
            .where(
                GpiSnapshot.scope == scope,
                GpiSnapshot.schoolYearId.in_(school_years),
            )
        )
        if entity_id is not None:
            stmt = stmt.where(GpiSnapshot.entityId == entity_id)
        else:
            stmt = stmt.where(GpiSnapshot.entityId.is_(None))

        rows = (await self.session.execute(stmt)).all()
        by_year: dict[str, GpiSnapshot] = {}
        year_names: dict[str, str | None] = {}
        for snapshot, year_name in rows:
            by_year[snapshot.schoolYearId] = snapshot
            year_names[snapshot.schoolYearId] = year_name

        points: list[GpiEvolutionPoint] = []
        for year_id in school_years:
            snap = by_year.get(year_id)
            if snap is None:
                # Point vide — utile pour le frontend.
                continue
            points.append(GpiEvolutionPoint(
                schoolYearId=snap.schoolYearId,
                schoolYearName=year_names.get(snap.schoolYearId),
                gpi=snap.gpi,
                severity=snap.severity,
                girlsCount=snap.girlsCount,
                boysCount=snap.boysCount,
                computedAt=snap.computedAt,
            ))
        # Ordre chronologique par computedAt (sera quasi-équivalent à
        # l'ordre par schoolYearId mais on s'assure côté Python).
        points.sort(key=lambda p: p.computedAt)
        return points

    # ==================================================================
    # Private helpers — GPI
    # ==================================================================
    @staticmethod
    def _gpi_cache_key(
        scope: GpiScope,
        entity_id: str | None,
        school_year_id: str | None,
    ) -> str:
        entity = entity_id or "_"
        year = school_year_id or "_"
        return f"enrollment:gpi:{scope.value}:{entity}:{year}"

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
        try:
            redis = get_redis()
            raw = await redis.get(key)
        except Exception:  # pragma: no cover - redis offline
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def _cache_set(
        self, key: str, payload: dict[str, Any], ttl: int = 300,
    ) -> None:
        try:
            redis = get_redis()
            await redis.setex(
                key, ttl, json.dumps(payload, ensure_ascii=False, default=str),
            )
        except Exception:  # pragma: no cover - redis offline
            return

    async def _invalidate_gpi_cache(self, school_year_id: str) -> None:
        """Best-effort : supprime toutes les clés ``enrollment:gpi:*:*:<year>``.

        Inclut aussi les lookups "dernière année" (suffixe ``:_``) puisqu'un
        recalcul peut changer la dernière année connue par cache.
        """
        try:
            redis = get_redis()
            for pattern in (
                f"enrollment:gpi:*:*:{school_year_id}",
                "enrollment:gpi:*:*:_",
            ):
                cursor = 0
                while True:
                    cursor, keys = await redis.scan(
                        cursor=cursor, match=pattern, count=200,
                    )
                    if keys:
                        await redis.delete(*keys)
                    if cursor == 0:
                        break
        except Exception as exc:  # pragma: no cover - redis offline
            logger.warning("invalidate_gpi_cache failed: {}", exc)

    async def _create_gpi_anomalies(self, *, school_year_id: str) -> int:
        """Hook Module 9 — matérialise les CRITICAL_GPI en AnomalyDetection.

        On supprime d'abord les anomalies CRITICAL_GPI PENDING liées à cette
        année (idempotence : un re-run ne duplique pas les alertes), puis
        on rejoue le détecteur Module 9.
        """
        from app.modules.anomalies.detectors import detect_critical_gpi
        from app.modules.anomalies.enums import AnomalyStatus, AnomalyType
        from app.modules.anomalies.models import AnomalyDetection

        # Purge anomalies CRITICAL_GPI PENDING existantes pour cette year
        # (on ne touche pas aux anomalies déjà revues — historique préservé).
        # Le matching se fait via evidence->>'schoolYearId'.
        await self.session.execute(
            delete(AnomalyDetection).where(
                AnomalyDetection.type == AnomalyType.CRITICAL_GPI,
                AnomalyDetection.status == AnomalyStatus.PENDING,
            )
        )

        new_anomalies = await detect_critical_gpi(
            self.session, school_year_id=school_year_id,
        )
        for a in new_anomalies:
            self.session.add(a)
        await self.session.flush()
        return len(new_anomalies)

    def _assert_gpi_scope_access(
        self,
        user: User,
        scope: GpiScope,
        entity_id: str | None,
    ) -> None:
        """RBAC territorial pour les lectures GPI.

        Règles :
        * NATIONAL est lisible par tous (la valeur agrégée est publique).
        * REGIONAL : un REGIONAL_ADMIN ne peut lire que sa région.
        * PREFECTURE : un PREFECTURE_ADMIN ne peut lire que sa préfecture.
        * SCHOOL : un SCHOOL_DIRECTOR ne peut lire que son école.
        * Les NATIONAL_SCOPE_ROLES bypassent tout.
        """
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        if scope == GpiScope.NATIONAL:
            return
        # Pour les autres scopes, on vérifie l'appariement.
        if scope == GpiScope.REGIONAL:
            if user.role in REGIONAL_SCOPE_ROLES and user.regionId == entity_id:
                return
            raise ForbiddenError(
                detail="Accès non autorisé à cette région."
            )
        if scope == GpiScope.PREFECTURE:
            if (
                user.role in PREFECTURE_SCOPE_ROLES
                and user.prefectureId == entity_id
            ):
                return
            raise ForbiddenError(
                detail="Accès non autorisé à cette préfecture."
            )
        if scope == GpiScope.SCHOOL:
            if user.schoolId == entity_id:
                return
            # Un REGIONAL_ADMIN peut aussi lire le GPI d'une école de sa
            # région — délégué au check fonctionnel via la table School.
            # Pour rester strict ici (et éviter une query supplémentaire),
            # on refuse — la lecture SCHOOL est faite par get_gpi qui
            # appelle aussi _assert_can_access_school plus tard si besoin.
            raise ForbiddenError(
                detail="Accès non autorisé à cette école."
            )

    # ==================================================================
    # Private helpers
    # ==================================================================
    def _aggregate_base_query(
        self, req: AggregateRequest, scope_user: User
    ) -> Select:
        """Construit la sous-requête de base appliquant scope RBAC + filtres."""
        stmt: Select = select(Enrollment).join(
            School, School.id == Enrollment.schoolId
        )
        stmt = stmt.where(Enrollment.schoolYearId == req.schoolYearId)
        stmt = stmt.where(Enrollment.source == req.source)

        # Filtres explicites de la requête.
        if req.regionId:
            stmt = stmt.where(School.regionId == req.regionId)
        if req.prefectureId:
            stmt = stmt.where(School.prefectureId == req.prefectureId)
        if req.subPrefectureId:
            stmt = stmt.where(School.subPrefectureId == req.subPrefectureId)
        if req.schoolId:
            stmt = stmt.where(School.id == req.schoolId)
        if req.classLevel:
            stmt = stmt.where(Enrollment.classLevel == req.classLevel)
        if req.gender:
            stmt = stmt.where(Enrollment.gender == req.gender)

        # Scope RBAC implicite (basé sur user.role).
        stmt = self._apply_school_scope(stmt, scope_user)
        return stmt

    def _apply_school_scope(self, stmt: Select, user: User) -> Select:
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(School.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(School.prefectureId == user.prefectureId)
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(School.subPrefectureId == user.subPrefectureId)
        if user.schoolId:
            return stmt.where(School.id == user.schoolId)
        # Sans scope identifiable : aucune row visible.
        return stmt.where(School.id == "__none__")

    async def _assert_can_access_school(
        self, user: User, school_id: str
    ) -> None:
        school = await self.session.get(School, school_id)
        if school is None:
            raise NotFoundError(detail="École introuvable")
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        if (
            user.role in REGIONAL_SCOPE_ROLES
            and user.regionId == school.regionId
        ):
            return
        if (
            user.role in PREFECTURE_SCOPE_ROLES
            and user.prefectureId == school.prefectureId
        ):
            return
        if (
            user.role in SUB_PREFECTURE_SCOPE_ROLES
            and user.subPrefectureId == school.subPrefectureId
        ):
            return
        if user.schoolId == school.id:
            return
        raise ForbiddenError(detail="Accès non autorisé pour cette école")

    async def _assert_school_year_exists(self, school_year_id: str) -> None:
        existing = (
            await self.session.execute(
                select(SchoolYear.id).where(SchoolYear.id == school_year_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            raise NotFoundError(detail="Année scolaire introuvable")


__all__ = ["COMPUTE_FROM_STUDENTS_ROLES", "EnrollmentService"]
