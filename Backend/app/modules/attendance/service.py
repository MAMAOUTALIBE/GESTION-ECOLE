"""Attendance service — today() + scan() with QR resolution + duplicate dedup.

Mirrors NestJS attendance.service.ts. The QR resolution + scope assertion are
delegated to ``CensusService`` to keep a single source of truth for credential
lookup and school access checks.
"""
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.observability import attendance_scan_total
from app.modules.attendance.models import AttendanceRecord
from app.modules.attendance.schemas import (
    AttendanceClassRoomBrief,
    AttendancePerson,
    AttendanceRecordRead,
    ScanAttendanceRequest,
    ScanAttendanceResponse,
)
from app.modules.auth.models import User
from app.modules.census.models import Student, Teacher
from app.modules.census.service import CensusService
from app.modules.schools.models import School
from app.modules.schools.schemas import SchoolEmbedded
from app.shared.enums import AttendanceStatus, PersonType
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)


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
