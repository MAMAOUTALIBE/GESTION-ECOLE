"""Module 1A — Service Enrollment.

Encapsule la logique métier des effectifs désagrégés :
* ``record`` : POST unitaire avec validation.
* ``bulk_record`` : POST groupé (max 200, atomic per-item).
* ``list_for_school`` : liste filtrée + scope RBAC automatique.
* ``aggregate`` : agrégations parallèles (niveau, genre, breakdown).
* ``compute_from_students`` : recalcule depuis la table Student (admin only).

Toutes les méthodes async. Les requêtes territoriales partagent les patterns
du module ``census`` (NATIONAL_SCOPE / REGIONAL_SCOPE / etc.).
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from app.modules.academics.models import SchoolYear
from app.modules.auth.models import User
from app.modules.census.models import Student
from app.modules.enrollment.enums import EnrollmentClassLevel, EnrollmentSource
from app.modules.enrollment.models import Enrollment
from app.modules.enrollment.schemas import (
    AggregateRequest,
    AggregateResponse,
    BulkItemError,
    BulkRecordResponse,
    EnrollmentAggregate,
    EnrollmentCreate,
    EnrollmentRead,
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
