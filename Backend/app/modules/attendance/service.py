"""Attendance service — today() + scan() with QR resolution + duplicate dedup.

Mirrors NestJS attendance.service.ts. The QR resolution + scope assertion are
delegated to ``CensusService`` to keep a single source of truth for credential
lookup and school access checks.

Module 3 ajoute :
* ``bulk_scan(records)`` : ingestion en lot (jusqu'à 200 scans/appel),
  idempotente (dédoublonnage par (student|teacher, jour calendaire)).
* ``attendance_stats(filters)`` : agrégation temporelle par bucket
  (day | week | month) avec cache Redis 60s.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ForbiddenError, NotFoundError, ValidationFailedError
from app.core.observability import attendance_scan_total
from app.core.redis import get_redis
from app.modules.attendance.models import AttendanceRecord
from app.modules.attendance.schemas import (
    AttendanceClassRoomBrief,
    AttendancePerson,
    AttendanceRecordRead,
    AttendanceStatsFilter,
    AttendanceStatsPeriod,
    AttendanceStatsPoint,
    AttendanceStatsResponse,
    AttendanceStatsTotals,
    BulkScanError,
    BulkScanRequest,
    BulkScanResult,
    ScanAttendanceRequest,
    ScanAttendanceResponse,
)
from app.modules.auth.models import User
from app.modules.census.models import Student, Teacher
from app.modules.census.service import CensusService
from app.modules.schools.models import ClassRoom, School
from app.modules.schools.schemas import SchoolEmbedded
from app.modules.workflow.models import AuditLog
from app.shared.enums import AttendanceStatus, PersonType
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)

STATS_CACHE_TTL_SECONDS = 60
STATS_CACHE_PREFIX = "attendance:stats:"


class AttendanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.census = CensusService(session)

    # ------------------------------------------------------------------
    async def today(self, user: User) -> list[AttendanceRecordRead]:
        start, end = self._today_range()
        stmt = (
            select(AttendanceRecord)
            .where(
                AttendanceRecord.scannedAt >= start,
                AttendanceRecord.scannedAt < end,
            )
            .order_by(AttendanceRecord.scannedAt.desc())
            .options(
                selectinload(AttendanceRecord.student).selectinload(Student.school).selectinload(
                    School.region
                ),
                selectinload(AttendanceRecord.student).selectinload(Student.classRoom),
                selectinload(AttendanceRecord.teacher).selectinload(Teacher.school).selectinload(
                    School.region
                ),
            )
        )
        stmt = self._scope_attendance_query(stmt, user)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [self._map_record(r) for r in rows]

    async def scan(
        self, user: User, dto: ScanAttendanceRequest
    ) -> ScanAttendanceResponse:
        try:
            credential = await self.census.resolve_credential(dto.qrToken)
        except NotFoundError:
            attendance_scan_total.labels(result="not_found").inc()
            raise

        if credential.personType == PersonType.STUDENT:
            person: Student | Teacher | None = credential.student
        else:
            person = credential.teacher
        if person is None:
            attendance_scan_total.labels(result="not_found").inc()
            raise NotFoundError(detail="Personne introuvable")

        try:
            await self.census.assert_can_access_school(user, person.schoolId)
        except ForbiddenError:
            attendance_scan_total.labels(result="forbidden").inc()
            raise

        start, end = self._today_range()
        dup_stmt = (
            select(AttendanceRecord)
            .where(
                AttendanceRecord.personType == credential.personType,
                AttendanceRecord.scannedAt >= start,
                AttendanceRecord.scannedAt < end,
            )
            .options(
                selectinload(AttendanceRecord.student).selectinload(Student.school).selectinload(
                    School.region
                ),
                selectinload(AttendanceRecord.student).selectinload(Student.classRoom),
                selectinload(AttendanceRecord.teacher).selectinload(Teacher.school).selectinload(
                    School.region
                ),
            )
        )
        if credential.personType == PersonType.STUDENT:
            dup_stmt = dup_stmt.where(AttendanceRecord.studentId == person.id)
        else:
            dup_stmt = dup_stmt.where(AttendanceRecord.teacherId == person.id)

        duplicate = (await self.session.execute(dup_stmt)).scalars().first()
        if duplicate is not None:
            attendance_scan_total.labels(result="duplicate").inc()
            return ScanAttendanceResponse(
                duplicate=True, record=self._map_record(duplicate)
            )

        record = AttendanceRecord(
            personType=credential.personType,
            status=dto.status or AttendanceStatus.PRESENT,
            scannedAt=datetime.now(UTC),
            schoolId=person.schoolId,
            studentId=person.id if credential.personType == PersonType.STUDENT else None,
            teacherId=person.id if credential.personType == PersonType.TEACHER else None,
        )
        self.session.add(record)
        await self.session.flush()

        # Reload with relations for the response payload
        reload_stmt = (
            select(AttendanceRecord)
            .where(AttendanceRecord.id == record.id)
            .options(
                selectinload(AttendanceRecord.student).selectinload(Student.school).selectinload(
                    School.region
                ),
                selectinload(AttendanceRecord.student).selectinload(Student.classRoom),
                selectinload(AttendanceRecord.teacher).selectinload(Teacher.school).selectinload(
                    School.region
                ),
            )
        )
        loaded = (await self.session.execute(reload_stmt)).scalar_one()
        attendance_scan_total.labels(result="ok").inc()
        return ScanAttendanceResponse(duplicate=False, record=self._map_record(loaded))

    # ------------------------------------------------------------------
    # Module 3 — bulk scan
    # ------------------------------------------------------------------
    async def bulk_scan(
        self, user: User, dto: BulkScanRequest
    ) -> BulkScanResult:
        """Ingestion en lot des scans pour une classe complète.

        Garanties :
        * Idempotent : un (studentId|teacherId, jour) déjà enregistré est
          skipped avec un compteur (jamais d'erreur dure).
        * Validation : pas de scannedAt dans le futur, et le scope
          territorial de l'utilisateur est appliqué sur chaque scan.
        * Audit : un seul ``AuditLog`` ``BULK_ATTENDANCE_SCAN`` agrégé
          décrit l'opération (count + by_status + schoolIds).
        """
        now = datetime.now(UTC)
        errors: list[BulkScanError] = []
        valid_items: list[tuple[int, Any]] = []  # (index, item)

        # Étape 1 — validation locale rapide (futur, scope partagé)
        for idx, item in enumerate(dto.items):
            if item.scannedAt > now:
                errors.append(
                    BulkScanError(index=idx, reason="scannedAt dans le futur")
                )
                continue
            valid_items.append((idx, item))

        if not valid_items:
            self.session.add(
                AuditLog(
                    actorId=user.id,
                    action="BULK_ATTENDANCE_SCAN",
                    entity="AttendanceRecord",
                    entityId=None,
                    metadata_={
                        "count": 0,
                        "errors": len(errors),
                        "schoolIds": [],
                        "by_status": {},
                    },
                )
            )
            return BulkScanResult(
                inserted=0, skipped=0, errors=errors, by_status={}
            )

        # Étape 2 — résoudre les personnes (student + teacher) en 2 requêtes
        student_ids = {i.studentId for _, i in valid_items if i.studentId}
        teacher_ids = {i.teacherId for _, i in valid_items if i.teacherId}

        students: dict[str, Student] = {}
        if student_ids:
            rows = await self.session.execute(
                select(Student).where(Student.id.in_(student_ids))
            )
            students = {s.id: s for s in rows.scalars().all()}
        teachers: dict[str, Teacher] = {}
        if teacher_ids:
            rows = await self.session.execute(
                select(Teacher).where(Teacher.id.in_(teacher_ids))
            )
            teachers = {t.id: t for t in rows.scalars().all()}

        # Étape 3 — résoudre les school_ids et vérifier le scope (1 fois /
        # school dans tout le batch).
        school_ids_seen: set[str] = set()
        for _, item in valid_items:
            person = (
                students.get(item.studentId or "")
                if item.studentId
                else teachers.get(item.teacherId or "")
            )
            if person is not None:
                school_ids_seen.add(person.schoolId)

        for sid in school_ids_seen:
            try:
                await self.census.assert_can_access_school(user, sid)
            except ForbiddenError as exc:
                # On invalide tout le batch (politique fail-fast pour la
                # cohérence : un bulk doit être atomique côté permissions).
                raise ForbiddenError(
                    detail=f"Accès refusé à l'école {sid}",
                    extra={"schoolId": sid},
                ) from exc

        # Étape 4 — calculer les couples (person_id, jour) déjà présents
        # pour skipper les doublons en un seul SELECT par catégorie.
        day_anchors: list[tuple[str, date]] = []
        for _, item in valid_items:
            day_anchors.append(
                (
                    item.studentId or item.teacherId or "",
                    item.scannedAt.astimezone(UTC).date(),
                )
            )

        existing_keys: set[tuple[str, date]] = set()
        if student_ids:
            existing_keys |= await self._existing_day_keys(
                AttendanceRecord.studentId, student_ids
            )
        if teacher_ids:
            existing_keys |= await self._existing_day_keys(
                AttendanceRecord.teacherId, teacher_ids
            )

        # Étape 5 — préparer les insertions
        to_insert: list[AttendanceRecord] = []
        by_status: dict[str, int] = {}
        skipped = 0
        seen_in_batch: set[tuple[str, date]] = set()
        for (idx, item), (person_id, day) in zip(valid_items, day_anchors, strict=True):
            if not person_id:
                errors.append(
                    BulkScanError(index=idx, reason="ni studentId ni teacherId")
                )
                continue
            person = (
                students.get(item.studentId or "")
                if item.studentId
                else teachers.get(item.teacherId or "")
            )
            if person is None:
                errors.append(
                    BulkScanError(index=idx, reason="personne introuvable")
                )
                continue
            key = (person_id, day)
            if key in existing_keys or key in seen_in_batch:
                skipped += 1
                continue
            seen_in_batch.add(key)

            person_type = (
                PersonType.STUDENT if item.studentId else PersonType.TEACHER
            )
            record = AttendanceRecord(
                personType=person_type,
                status=item.status,
                scannedAt=item.scannedAt,
                schoolId=person.schoolId,
                studentId=item.studentId,
                teacherId=item.teacherId,
            )
            to_insert.append(record)
            by_status[item.status.value] = by_status.get(item.status.value, 0) + 1

        if to_insert:
            self.session.add_all(to_insert)
            await self.session.flush()

        # Étape 6 — audit
        self.session.add(
            AuditLog(
                actorId=user.id,
                action="BULK_ATTENDANCE_SCAN",
                entity="AttendanceRecord",
                entityId=None,
                metadata_={
                    "count": len(to_insert),
                    "skipped": skipped,
                    "errors": len(errors),
                    "schoolIds": sorted(school_ids_seen),
                    "by_status": by_status,
                },
            )
        )
        await self.session.flush()

        for _ in to_insert:
            attendance_scan_total.labels(result="ok").inc()
        for _ in range(skipped):
            attendance_scan_total.labels(result="duplicate").inc()

        # Module 13 — publication d'événement temps réel (best-effort).
        # On émet UN event par école touchée — un bulk de 200 scans sur 3
        # écoles → 3 events agrégés, jamais 200. Le cockpit veut un signal
        # "il se passe quelque chose ici" pas un firehose de scans.
        if to_insert:
            try:
                from app.modules.realtime.service import RealtimeService
                from app.modules.schools.models import School as _School

                # Resolve regionId par school (1 SELECT pour la liste).
                schools_seen = sorted(school_ids_seen)
                region_by_school: dict[str, str] = {}
                if schools_seen:
                    rows = await self.session.execute(
                        select(_School.id, _School.regionId).where(
                            _School.id.in_(schools_seen)
                        )
                    )
                    for sid, rid in rows.all():
                        region_by_school[sid] = rid
                # Count par école
                count_by_school: dict[str, int] = {}
                for rec in to_insert:
                    count_by_school[rec.schoolId] = (
                        count_by_school.get(rec.schoolId, 0) + 1
                    )
                for sid, count in count_by_school.items():
                    await RealtimeService.publish_attendance_scan(
                        school_id=sid,
                        region_id=region_by_school.get(sid),
                        count=count,
                    )
            except Exception:  # pragma: no cover — best-effort
                pass

        return BulkScanResult(
            inserted=len(to_insert),
            skipped=skipped,
            errors=errors,
            by_status=by_status,
        )

    async def _existing_day_keys(
        self, column: Any, person_ids: set[str]
    ) -> set[tuple[str, date]]:
        """Renvoie l'ensemble (person_id, jour_utc) déjà présent en base
        pour les personnes données. Une seule requête, agrégée par jour.

        Important : on tronque en UTC explicitement (``AT TIME ZONE 'UTC'``)
        car la TZ par défaut du serveur peut être Europe/Paris ou
        Africa/Conakry — sans cast UTC, le jour postgres ne coïnciderait
        pas avec ``item.scannedAt.astimezone(UTC).date()`` côté Python.
        """
        if not person_ids:
            return set()
        # SCANNEDAT AT TIME ZONE 'UTC' :: date
        utc_day = func.date_trunc(
            "day",
            func.timezone("UTC", AttendanceRecord.scannedAt),
        )
        rows = await self.session.execute(
            select(column, utc_day)
            .where(column.in_(person_ids))
            .group_by(column, utc_day)
        )
        out: set[tuple[str, date]] = set()
        for pid, day_value in rows.all():
            if isinstance(day_value, datetime):
                out.add((pid, day_value.date()))
            elif isinstance(day_value, date):
                out.add((pid, day_value))
        return out

    # ------------------------------------------------------------------
    # Module 3 — stats agrégées
    # ------------------------------------------------------------------
    async def attendance_stats(
        self, user: User, filters: AttendanceStatsFilter
    ) -> AttendanceStatsResponse:
        """Statistiques de présence agrégées par bucket temporel.

        Sécurité : on applique systématiquement le scope territorial de
        l'utilisateur en plus du filtre fourni (un directeur ne peut pas
        forcer schoolId=autre).

        Cache : clé dérivée d'un hash stable des filtres + role+scope
        utilisateur ; TTL 60s. Toute écriture invalide naturellement (TTL
        court — pas d'invalidation explicite nécessaire pour Module 3).
        """
        # 1. Resolve scope-aware school filter
        scoped_schools = await self._resolve_scoped_school_ids(
            user, filters.schoolId, filters.classRoomId, filters.studentId
        )

        # 2. Try cache
        cache_key = self._stats_cache_key(user, filters, scoped_schools)
        redis = get_redis()
        cached: str | None = None
        with contextlib.suppress(Exception):  # pragma: no cover - redis transient
            cached = await redis.get(cache_key)
        if cached:
            with contextlib.suppress(Exception):  # pragma: no cover - cache corruption
                payload = json.loads(cached)
                return AttendanceStatsResponse.model_validate(payload)

        # 3. Build aggregation query
        bucket = self._bucket_expression(filters.groupBy)
        start_dt = datetime.combine(filters.dateFrom, time.min, tzinfo=UTC)
        # +1 jour pour inclure dateTo en entier (borne droite exclusive)
        end_dt = datetime.combine(
            filters.dateTo + timedelta(days=1), time.min, tzinfo=UTC
        )

        stmt = (
            select(
                bucket.label("bucket"),
                AttendanceRecord.status,
                func.count().label("n"),
            )
            .where(
                AttendanceRecord.scannedAt >= start_dt,
                AttendanceRecord.scannedAt < end_dt,
            )
            .group_by("bucket", AttendanceRecord.status)
            .order_by("bucket")
        )

        # 3a. apply filters
        if scoped_schools is not None:
            if not scoped_schools:
                # scope vide → renvoyer une réponse vide propre
                empty = AttendanceStatsResponse(
                    series=[],
                    totals=AttendanceStatsTotals(),
                    attendanceRate=0.0,
                    period=AttendanceStatsPeriod(
                        dateFrom=filters.dateFrom,
                        dateTo=filters.dateTo,
                        groupBy=filters.groupBy,
                    ),
                )
                await self._set_cache(redis, cache_key, empty)
                return empty
            stmt = stmt.where(AttendanceRecord.schoolId.in_(scoped_schools))
        if filters.classRoomId:
            stmt = stmt.where(
                AttendanceRecord.studentId.in_(
                    select(Student.id).where(
                        Student.classRoomId == filters.classRoomId
                    )
                )
            )
        if filters.studentId:
            stmt = stmt.where(AttendanceRecord.studentId == filters.studentId)

        rows = await self.session.execute(stmt)

        # 4. Reduce rows into series points
        series_map: dict[date, AttendanceStatsPoint] = {}
        totals = AttendanceStatsTotals()
        for bucket_value, status, n in rows.all():
            bucket_date = (
                bucket_value.date()
                if isinstance(bucket_value, datetime)
                else bucket_value
            )
            point = series_map.setdefault(
                bucket_date, AttendanceStatsPoint(date=bucket_date)
            )
            count = int(n or 0)
            totals.total += count
            if status == AttendanceStatus.PRESENT:
                point.present += count
                totals.present += count
            elif status == AttendanceStatus.ABSENT:
                point.absent += count
                totals.absent += count
            elif status == AttendanceStatus.LATE:
                point.late += count
                totals.late += count

        series = sorted(series_map.values(), key=lambda p: p.date)
        rate = (
            (totals.present + totals.late) / totals.total
            if totals.total > 0
            else 0.0
        )
        response = AttendanceStatsResponse(
            series=series,
            totals=totals,
            attendanceRate=round(rate, 4),
            period=AttendanceStatsPeriod(
                dateFrom=filters.dateFrom,
                dateTo=filters.dateTo,
                groupBy=filters.groupBy,
            ),
        )

        await self._set_cache(redis, cache_key, response)
        return response

    @staticmethod
    async def _set_cache(
        redis: Any, key: str, response: AttendanceStatsResponse
    ) -> None:
        with contextlib.suppress(Exception):  # pragma: no cover - redis transient
            await redis.set(
                key,
                response.model_dump_json(),
                ex=STATS_CACHE_TTL_SECONDS,
            )

    @staticmethod
    def _stats_cache_key(
        user: User,
        filters: AttendanceStatsFilter,
        scoped_schools: list[str] | None,
    ) -> str:
        payload = {
            "f": filters.model_dump(mode="json"),
            "r": user.role.value if user.role else None,
            "sc": sorted(scoped_schools) if scoped_schools is not None else None,
        }
        # Hash stable (sort_keys=True) pour ne pas dépendre de l'ordre des dict
        blob = json.dumps(payload, sort_keys=True, default=str)
        digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()
        return f"{STATS_CACHE_PREFIX}{digest}"

    @staticmethod
    def _bucket_expression(group_by: str) -> Any:
        # date_trunc côté DB → exploite le partition pruning ; aucune
        # transformation Python sur des millions de lignes.
        # Cast UTC explicite : la TZ du serveur peut être Europe/Paris,
        # on garantit que le bucket "day" colle aux jours UTC (les dates
        # côté Pydantic sont en UTC implicite).
        col = func.timezone("UTC", AttendanceRecord.scannedAt)
        if group_by == "day":
            return func.date_trunc("day", col)
        if group_by == "week":
            return func.date_trunc("week", col)
        if group_by == "month":
            return func.date_trunc("month", col)
        raise ValidationFailedError(detail=f"groupBy invalide: {group_by}")

    async def _resolve_scoped_school_ids(
        self,
        user: User,
        school_id: str | None,
        class_room_id: str | None,
        student_id: str | None,
    ) -> list[str] | None:
        """Renvoie la liste des schoolIds que l'utilisateur peut voir,
        intersectée avec la cible (school/classRoom/student) demandée.

        Convention :
        * ``None`` → pas de filtre school (national admin, pas de cible
          school explicite — uniquement studentId/classRoomId qui se
          résolvent via une sous-requête).
        * Liste vide → l'utilisateur ne peut rien voir → réponse vide.
        """
        # Charger la classroom / student pour récupérer leur schoolId si
        # la cible est plus fine qu'une école.
        target_school_id = school_id
        if target_school_id is None and class_room_id is not None:
            cls = await self.session.get(ClassRoom, class_room_id)
            if cls is None:
                raise NotFoundError(detail="ClassRoom introuvable")
            target_school_id = cls.schoolId
        if target_school_id is None and student_id is not None:
            stu = await self.session.get(Student, student_id)
            if stu is None:
                raise NotFoundError(detail="Student introuvable")
            target_school_id = stu.schoolId

        if target_school_id is not None:
            # Le scope doit *autoriser* cette école.
            try:
                await self.census.assert_can_access_school(user, target_school_id)
            except ForbiddenError:
                return []
            return [target_school_id]

        # Pas de cible précise → on borne par le scope de l'utilisateur.
        if user.role in NATIONAL_SCOPE_ROLES:
            return None
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            rows = await self.session.execute(
                select(School.id).where(School.regionId == user.regionId)
            )
            return list(rows.scalars().all())
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            rows = await self.session.execute(
                select(School.id).where(School.prefectureId == user.prefectureId)
            )
            return list(rows.scalars().all())
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            rows = await self.session.execute(
                select(School.id).where(
                    School.subPrefectureId == user.subPrefectureId
                )
            )
            return list(rows.scalars().all())
        if user.schoolId:
            return [user.schoolId]
        return []

    # ------------------------------------------------------------------
    def _scope_attendance_query(self, stmt: Any, user: User) -> Any:
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(
                or_(
                    AttendanceRecord.studentId.in_(
                        select(Student.id).where(
                            Student.schoolId.in_(
                                select(School.id).where(School.regionId == user.regionId)
                            )
                        )
                    ),
                    AttendanceRecord.teacherId.in_(
                        select(Teacher.id).where(
                            Teacher.schoolId.in_(
                                select(School.id).where(School.regionId == user.regionId)
                            )
                        )
                    ),
                )
            )
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(
                or_(
                    AttendanceRecord.studentId.in_(
                        select(Student.id).where(
                            Student.schoolId.in_(
                                select(School.id).where(
                                    School.prefectureId == user.prefectureId
                                )
                            )
                        )
                    ),
                    AttendanceRecord.teacherId.in_(
                        select(Teacher.id).where(
                            Teacher.schoolId.in_(
                                select(School.id).where(
                                    School.prefectureId == user.prefectureId
                                )
                            )
                        )
                    ),
                )
            )
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(
                or_(
                    AttendanceRecord.studentId.in_(
                        select(Student.id).where(
                            Student.schoolId.in_(
                                select(School.id).where(
                                    School.subPrefectureId == user.subPrefectureId
                                )
                            )
                        )
                    ),
                    AttendanceRecord.teacherId.in_(
                        select(Teacher.id).where(
                            Teacher.schoolId.in_(
                                select(School.id).where(
                                    School.subPrefectureId == user.subPrefectureId
                                )
                            )
                        )
                    ),
                )
            )
        if user.schoolId:
            return stmt.where(AttendanceRecord.schoolId == user.schoolId)
        # fallback: no scope -> no results
        return stmt.where(and_(False))

    @staticmethod
    def _today_range() -> tuple[datetime, datetime]:
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)

    # ------------------------------------------------------------------
    @staticmethod
    def _map_record(record: AttendanceRecord) -> AttendanceRecordRead:
        person_obj: Student | Teacher | None
        if record.personType == PersonType.STUDENT:
            person_obj = record.student
        else:
            person_obj = record.teacher

        if person_obj is None:
            return AttendanceRecordRead(
                id=record.id,
                personType=record.personType,
                status=record.status,
                scannedAt=record.scannedAt,
                person=None,
            )

        school_payload = (
            SchoolEmbedded.model_validate(person_obj.school)
            if person_obj.school
            else None
        )
        class_payload: AttendanceClassRoomBrief | None = None
        if (
            isinstance(person_obj, Student)
            and person_obj.classRoom is not None
        ):
            class_payload = AttendanceClassRoomBrief.model_validate(
                person_obj.classRoom
            )

        return AttendanceRecordRead(
            id=record.id,
            personType=record.personType,
            status=record.status,
            scannedAt=record.scannedAt,
            person=AttendancePerson(
                id=person_obj.id,
                uniqueCode=person_obj.uniqueCode,
                firstName=person_obj.firstName,
                lastName=person_obj.lastName,
                fullName=f"{person_obj.firstName} {person_obj.lastName}",
                school=school_payload,
                classRoom=class_payload,
            ),
        )


__all__ = ["STATS_CACHE_PREFIX", "STATS_CACHE_TTL_SECONDS", "AttendanceService"]
