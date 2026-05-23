"""Census service — students, teachers, dashboard, metadata.

Notes
-----
* QR SVG rendering is intentionally deferred to Phase 5 (attendance scan).
  Student/Teacher creation still generates a QrCredential row + token, but
  responses return ``qrSvg=None``. The /api/census/identify and /api/census/qr
  endpoints will be implemented alongside the attendance flow.
* All write operations record an AuditLog row matching the NestJS contract.
"""
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import qrcode
import qrcode.image.svg
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.modules.attendance.models import AttendanceRecord, QrCredential
from app.modules.auth.models import User
from app.modules.census.models import Student, StudentTransfer, Teacher
from app.modules.census.schemas import (
    AssignStudentClassRequest,
    AssignTeacherClassesRequest,
    ClassRoomSummary,
    CreateStudentRequest,
    CreateTeacherRequest,
    DashboardAlert,
    DashboardByRegion,
    DashboardByTerritory,
    DashboardCapacity,
    DashboardDataQuality,
    DashboardOverloadedClass,
    DashboardQuery,
    DashboardRatios,
    DashboardResponse,
    DashboardTerritory,
    DashboardTopSchool,
    DashboardTotals,
    IdentifyResponse,
    MetadataResponse,
    QrSvgResponse,
    RecentAttendance,
    StudentRead,
    TeacherRead,
    TransferHistoryItem,
    TransferStudentRequest,
)
from app.modules.schools.models import ClassRoom, School, class_room_teacher_table
from app.modules.schools.schemas import SchoolEmbedded, TerritorialBriefRead
from app.modules.territory.models import Prefecture, Region, SubPrefecture
from app.modules.territory.schemas import (
    PrefectureRead,
    RegionRead,
    SubPrefectureRead,
)
from app.modules.workflow.models import AuditLog
from app.modules.workflow.service import ValidationTarget, WorkflowService
from app.shared.enums import (
    AttendanceStatus,
    PersonType,
    UserRole,
    ValidationEntityType,
    ValidationStatus,
)
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


class CensusService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.workflow = WorkflowService(session)

    # ==================================================================
    # STUDENTS
    # ==================================================================
    async def list_students(
        self, user: User, *, limit: int = 500,
    ) -> list[StudentRead]:
        stmt = (
            select(Student)
            .options(
                selectinload(Student.school).selectinload(School.region),
                selectinload(Student.classRoom),
                selectinload(Student.qrCredential),
                selectinload(Student.transferHistory).selectinload(StudentTransfer.fromSchool),
                selectinload(Student.transferHistory).selectinload(StudentTransfer.toSchool),
                selectinload(Student.transferHistory).selectinload(StudentTransfer.fromClassRoom),
                selectinload(Student.transferHistory).selectinload(StudentTransfer.toClassRoom),
                selectinload(Student.transferHistory).selectinload(StudentTransfer.actor),
            )
            .order_by(Student.createdAt.desc())
            .limit(limit)
        )
        stmt = self._scope_person_query(stmt, user, model=Student)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [self._map_student(s) for s in rows]

    async def list_student_cards(self, user: User) -> list[StudentRead]:
        stmt = (
            select(Student)
            .options(
                selectinload(Student.school).selectinload(School.region),
                selectinload(Student.classRoom),
                selectinload(Student.qrCredential),
            )
            .order_by(Student.lastName.asc(), Student.firstName.asc())
        )
        stmt = self._scope_person_query(stmt, user, model=Student)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [self._map_student(s) for s in rows]

    async def get_student(self, user: User, student_id: str) -> StudentRead:
        stmt = (
            select(Student)
            .where(Student.id == student_id)
            .options(
                selectinload(Student.school).selectinload(School.region),
                selectinload(Student.classRoom),
                selectinload(Student.qrCredential),
            )
        )
        student = (await self.session.execute(stmt)).scalar_one_or_none()
        if student is None:
            raise NotFoundError(detail="Élève introuvable")
        await self._assert_can_access_school(user, student.schoolId)
        return self._map_student(student)

    async def create_student(self, user: User, dto: CreateStudentRequest) -> StudentRead:
        await self._assert_can_access_school(user, dto.schoolId)
        await self._assert_no_duplicate_student(dto)
        if dto.classRoomId:
            await self._assert_class_belongs_to_school(dto.classRoomId, dto.schoolId)

        unique_code = await self._generate_unique_code(PersonType.STUDENT, dto.schoolId)
        student = Student(
            uniqueCode=unique_code,
            firstName=dto.firstName.strip(),
            lastName=dto.lastName.strip(),
            gender=dto.gender,
            birthDate=datetime.combine(dto.birthDate, datetime.min.time(), tzinfo=UTC)
            if dto.birthDate
            else None,
            photoUrl=_clean(dto.photoUrl),
            guardianName=_clean(dto.guardianName),
            guardianPhone=_clean(dto.guardianPhone),
            schoolId=dto.schoolId,
            classRoomId=dto.classRoomId,
        )
        self.session.add(student)
        await self.session.flush()

        self.session.add(
            QrCredential(
                token=uuid4().hex,
                payload=unique_code,
                personType=PersonType.STUDENT,
                studentId=student.id,
            )
        )
        self.session.add(
            AuditLog(
                actorId=user.id,
                action="CREATE_STUDENT",
                entity="Student",
                entityId=student.id,
                metadata_={"uniqueCode": unique_code},
            )
        )
        await self.session.flush()
        return await self.get_student(user, student.id)

    async def assign_student_class(
        self, user: User, student_id: str, dto: AssignStudentClassRequest
    ) -> StudentRead:
        student = await self.session.get(Student, student_id)
        if student is None:
            raise NotFoundError(detail="Élève introuvable")
        await self._assert_can_access_school(user, student.schoolId)

        new_class_id = _clean(dto.classRoomId)
        if new_class_id:
            await self._assert_class_belongs_to_school(new_class_id, student.schoolId)

        student.classRoomId = new_class_id
        self.session.add(
            AuditLog(
                actorId=user.id,
                action="ASSIGN_STUDENT_CLASS",
                entity="Student",
                entityId=student.id,
                metadata_={"classRoomId": new_class_id},
            )
        )
        await self.session.flush()
        return await self.get_student(user, student.id)

    async def transfer_student(
        self, user: User, student_id: str, dto: TransferStudentRequest
    ) -> StudentRead:
        student = await self.session.get(Student, student_id)
        if student is None:
            raise NotFoundError(detail="Élève introuvable")

        await self._assert_can_access_school(user, student.schoolId)
        await self._assert_can_access_school(user, dto.toSchoolId)

        target_class_id = _clean(dto.toClassRoomId)
        if target_class_id:
            await self._assert_class_belongs_to_school(target_class_id, dto.toSchoolId)

        from_school_id = student.schoolId
        from_class_id = student.classRoomId

        self.session.add(
            StudentTransfer(
                studentId=student.id,
                fromSchoolId=from_school_id,
                toSchoolId=dto.toSchoolId,
                fromClassRoomId=from_class_id,
                toClassRoomId=target_class_id,
                reason=_clean(dto.reason),
                actorId=user.id,
                transferredAt=datetime.now(UTC),
            )
        )
        student.schoolId = dto.toSchoolId
        student.classRoomId = target_class_id

        self.session.add(
            AuditLog(
                actorId=user.id,
                action="TRANSFER_STUDENT",
                entity="Student",
                entityId=student.id,
                metadata_={
                    "fromSchoolId": from_school_id,
                    "toSchoolId": dto.toSchoolId,
                    "fromClassRoomId": from_class_id,
                    "toClassRoomId": target_class_id,
                },
            )
        )
        await self.session.flush()

        # Reload with full transfer history for the response
        stmt = (
            select(Student)
            .where(Student.id == student.id)
            .options(
                selectinload(Student.school).selectinload(School.region),
                selectinload(Student.classRoom),
                selectinload(Student.qrCredential),
                selectinload(Student.transferHistory).selectinload(StudentTransfer.fromSchool),
                selectinload(Student.transferHistory).selectinload(StudentTransfer.toSchool),
                selectinload(Student.transferHistory).selectinload(StudentTransfer.fromClassRoom),
                selectinload(Student.transferHistory).selectinload(StudentTransfer.toClassRoom),
                selectinload(Student.transferHistory).selectinload(StudentTransfer.actor),
            )
        )
        loaded = (await self.session.execute(stmt)).scalar_one()
        return self._map_student(loaded)

    # ==================================================================
    # TEACHERS
    # ==================================================================
    async def list_teachers(self, user: User) -> list[TeacherRead]:
        stmt = (
            select(Teacher)
            .options(
                selectinload(Teacher.school).selectinload(School.region),
                selectinload(Teacher.classes).selectinload(ClassRoom.school).selectinload(
                    School.region
                ),
                selectinload(Teacher.qrCredential),
            )
            .order_by(Teacher.createdAt.desc())
        )
        stmt = self._scope_person_query(stmt, user, model=Teacher)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [self._map_teacher(t) for t in rows]

    async def list_teacher_cards(self, user: User) -> list[TeacherRead]:
        stmt = (
            select(Teacher)
            .options(
                selectinload(Teacher.school).selectinload(School.region),
                selectinload(Teacher.classes).selectinload(ClassRoom.school).selectinload(
                    School.region
                ),
                selectinload(Teacher.qrCredential),
            )
            .order_by(Teacher.lastName.asc(), Teacher.firstName.asc())
        )
        stmt = self._scope_person_query(stmt, user, model=Teacher)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [self._map_teacher(t) for t in rows]

    async def get_teacher(self, user: User, teacher_id: str) -> TeacherRead:
        stmt = (
            select(Teacher)
            .where(Teacher.id == teacher_id)
            .options(
                selectinload(Teacher.school).selectinload(School.region),
                selectinload(Teacher.classes).selectinload(ClassRoom.school).selectinload(
                    School.region
                ),
                selectinload(Teacher.qrCredential),
            )
        )
        teacher = (await self.session.execute(stmt)).scalar_one_or_none()
        if teacher is None:
            raise NotFoundError(detail="Enseignant introuvable")
        await self._assert_can_access_school(user, teacher.schoolId)
        return self._map_teacher(teacher)

    async def create_teacher(self, user: User, dto: CreateTeacherRequest) -> TeacherRead:
        await self._assert_can_access_school(user, dto.schoolId)
        await self._assert_no_duplicate_teacher(dto)
        if dto.classRoomIds:
            await self._assert_classes_belong_to_school(dto.classRoomIds, dto.schoolId)

        school = await self.session.get(School, dto.schoolId)
        if school is None:
            raise NotFoundError(detail="École introuvable")
        review_target = self._teacher_review_target(user, school)
        is_review = review_target is not None

        unique_code = await self._generate_unique_code(PersonType.TEACHER, dto.schoolId)
        teacher = Teacher(
            uniqueCode=unique_code,
            firstName=dto.firstName.strip(),
            lastName=dto.lastName.strip(),
            gender=dto.gender,
            birthDate=datetime.combine(dto.birthDate, datetime.min.time(), tzinfo=UTC)
            if dto.birthDate
            else None,
            photoUrl=_clean(dto.photoUrl),
            phone=_clean(dto.phone),
            subject=_clean(dto.subject),
            diploma=_clean(dto.diploma),
            schoolId=dto.schoolId,
            status=ValidationStatus.SUBMITTED if is_review else ValidationStatus.APPROVED,
            createdById=user.id,
            approvedById=None if is_review else user.id,
            approvedAt=None if is_review else datetime.now(UTC),
        )
        self.session.add(teacher)
        await self.session.flush()

        if dto.classRoomIds:
            from sqlalchemy import insert

            await self.session.execute(
                insert(class_room_teacher_table),
                [{"A": cid, "B": teacher.id} for cid in dto.classRoomIds],
            )

        self.session.add(
            QrCredential(
                token=uuid4().hex,
                payload=unique_code,
                personType=PersonType.TEACHER,
                teacherId=teacher.id,
            )
        )
        self.session.add(
            AuditLog(
                actorId=user.id,
                action="CREATE_TEACHER",
                entity="Teacher",
                entityId=teacher.id,
                metadata_={"uniqueCode": unique_code},
            )
        )
        await self.session.flush()

        if is_review and review_target is not None:
            await self.workflow.create_validation_request(
                ValidationTarget(
                    entity_type=ValidationEntityType.TEACHER,
                    entity_id=teacher.id,
                    requested_by_id=user.id,
                    reviewer_role=review_target["role"],
                    reviewer_prefecture_id=review_target.get("prefectureId"),
                    reviewer_sub_prefecture_id=review_target.get("subPrefectureId"),
                    title="Nouvel enseignant à valider",
                    message=(
                        f"{user.fullName} demande la validation de l'enseignant "
                        f"{teacher.firstName} {teacher.lastName}."
                    ),
                )
            )

        return await self.get_teacher(user, teacher.id)

    async def assign_teacher_classes(
        self, user: User, teacher_id: str, dto: AssignTeacherClassesRequest
    ) -> TeacherRead:
        teacher = await self.session.get(Teacher, teacher_id)
        if teacher is None:
            raise NotFoundError(detail="Enseignant introuvable")
        await self._assert_can_access_school(user, teacher.schoolId)
        await self._assert_classes_belong_to_school(dto.classRoomIds, teacher.schoolId)

        from sqlalchemy import delete, insert

        await self.session.execute(
            delete(class_room_teacher_table).where(teacher_id == class_room_teacher_table.c.B)
        )
        if dto.classRoomIds:
            await self.session.execute(
                insert(class_room_teacher_table),
                [{"A": cid, "B": teacher.id} for cid in dto.classRoomIds],
            )

        self.session.add(
            AuditLog(
                actorId=user.id,
                action="ASSIGN_TEACHER_CLASSES",
                entity="Teacher",
                entityId=teacher.id,
                metadata_={"classRoomIds": dto.classRoomIds},
            )
        )
        await self.session.flush()
        return await self.get_teacher(user, teacher.id)

    # ==================================================================
    # QR / IDENTIFY (used by attendance + cards)
    # ==================================================================
    async def resolve_credential(self, value: str) -> QrCredential:
        """Resolve a token / payload / uniqueCode (or URL containing one) to
        a non-revoked QrCredential. Raises NotFoundError if unknown.
        """
        candidates = self._qr_candidates(value)
        stmt = (
            select(QrCredential)
            .where(QrCredential.revokedAt.is_(None))
            .where(
                or_(
                    QrCredential.token.in_(candidates),
                    QrCredential.payload.in_(candidates),
                    QrCredential.studentId.in_(
                        select(Student.id).where(Student.uniqueCode.in_(candidates))
                    ),
                    QrCredential.teacherId.in_(
                        select(Teacher.id).where(Teacher.uniqueCode.in_(candidates))
                    ),
                )
            )
            .options(
                selectinload(QrCredential.student).selectinload(Student.school).selectinload(
                    School.region
                ),
                selectinload(QrCredential.student).selectinload(Student.classRoom),
                selectinload(QrCredential.student).selectinload(Student.qrCredential),
                selectinload(QrCredential.teacher).selectinload(Teacher.school).selectinload(
                    School.region
                ),
                selectinload(QrCredential.teacher).selectinload(Teacher.classes).selectinload(
                    ClassRoom.school
                ).selectinload(School.region),
                selectinload(QrCredential.teacher).selectinload(Teacher.qrCredential),
            )
        )
        credential = (await self.session.execute(stmt)).scalars().first()
        if credential is None:
            raise NotFoundError(detail="QR code introuvable ou révoqué")
        return credential

    async def assert_can_access_school(self, user: User, school_id: str) -> None:
        """Public alias for the cross-module scope guard."""
        await self._assert_can_access_school(user, school_id)

    async def identify(self, user: User, token_or_code: str) -> IdentifyResponse:
        credential = await self.resolve_credential(token_or_code)
        school_id = (
            credential.student.schoolId if credential.student
            else (credential.teacher.schoolId if credential.teacher else None)
        )
        if school_id is None:
            raise NotFoundError(detail="Personne introuvable")
        await self._assert_can_access_school(user, school_id)

        person: StudentRead | TeacherRead | None = None
        if credential.personType == PersonType.STUDENT and credential.student:
            person = self._map_student(credential.student)
        elif credential.personType == PersonType.TEACHER and credential.teacher:
            person = self._map_teacher(credential.teacher)
        return IdentifyResponse(personType=credential.personType, person=person)

    async def qr_svg(self, user: User, token: str) -> QrSvgResponse:
        identified = await self.identify(user, token)
        payload = (
            identified.person.uniqueCode if identified.person is not None else token
        )
        svg = self._render_qr_svg(payload)
        return QrSvgResponse(
            personType=identified.personType,
            person=identified.person,
            qrSvg=svg,
        )

    @staticmethod
    def _qr_candidates(value: str) -> list[str]:
        cleaned = value.strip()
        without_query = cleaned.split("?", 1)[0]
        last_segment = ""
        for part in reversed(without_query.split("/")):
            if part:
                last_segment = part
                break
        seen: dict[str, None] = {}
        for c in (cleaned, last_segment):
            if c and c not in seen:
                seen[c] = None
        return list(seen.keys())

    @staticmethod
    def _render_qr_svg(payload: str) -> str:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=1,
        )
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(image_factory=qrcode.image.svg.SvgImage)
        from io import BytesIO

        buf = BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")

    # ==================================================================
    # METADATA + DASHBOARD
    # ==================================================================
    async def metadata(self, user: User) -> MetadataResponse:
        # Regions in scope
        regions_stmt = self._scope_region_query(
            select(Region).options(selectinload(Region.schools).selectinload(School.region)),
            user,
        ).order_by(Region.name.asc())
        regions = (await self.session.execute(regions_stmt)).scalars().unique().all()

        # Schools in scope
        schools_stmt = self._scope_school_query(
            select(School).options(selectinload(School.region)), user
        ).order_by(School.name.asc())
        schools = (await self.session.execute(schools_stmt)).scalars().unique().all()

        # Prefectures in scope
        pref_stmt = self._scope_prefecture_query(
            select(Prefecture).options(selectinload(Prefecture.region)), user
        ).order_by(Prefecture.name.asc())
        prefectures = (await self.session.execute(pref_stmt)).scalars().unique().all()

        # Sub-prefectures in scope
        sub_stmt = self._scope_sub_prefecture_query(
            select(SubPrefecture).options(
                selectinload(SubPrefecture.prefecture).selectinload(Prefecture.region)
            ),
            user,
        ).order_by(SubPrefecture.name.asc())
        sub_prefectures = (await self.session.execute(sub_stmt)).scalars().unique().all()

        return MetadataResponse(
            regions=[RegionRead.model_validate(r) for r in regions],
            schools=[SchoolEmbedded.model_validate(s) for s in schools],
            prefectures=[PrefectureRead.model_validate(p) for p in prefectures],
            subPrefectures=[SubPrefectureRead.model_validate(s) for s in sub_prefectures],
            roles=[r.value for r in UserRole],
        )

    async def dashboard(
        self, user: User, filters: DashboardQuery
    ) -> DashboardResponse:
        active_filters = DashboardQuery(
            regionId=_clean(filters.regionId),
            prefecture=_clean(filters.prefecture),
            commune=_clean(filters.commune),
            schoolId=_clean(filters.schoolId),
        )

        # School scope (combined with filters)
        def school_filter(stmt):  # type: ignore[no-untyped-def]
            stmt = self._scope_school_query(stmt, user)
            stmt = stmt.where(School.status == ValidationStatus.APPROVED)
            if active_filters.regionId:
                stmt = stmt.where(School.regionId == active_filters.regionId)
            if active_filters.prefecture:
                stmt = stmt.where(School.prefecture == active_filters.prefecture)
            if active_filters.commune:
                stmt = stmt.where(School.commune == active_filters.commune)
            if active_filters.schoolId:
                stmt = stmt.where(School.id == active_filters.schoolId)
            return stmt

        scoped_school_ids_subq = school_filter(select(School.id)).subquery()
        scoped_school_ids = select(scoped_school_ids_subq.c.id)

        # COUNTS
        students = await self._scalar_count(Student.schoolId.in_(scoped_school_ids))
        teachers = await self._scalar_count(
            Teacher.schoolId.in_(scoped_school_ids), model=Teacher
        )
        schools = await self._scalar_count(School.id.in_(scoped_school_ids), model=School)
        classes = await self._scalar_count(
            ClassRoom.schoolId.in_(scoped_school_ids), model=ClassRoom
        )

        # Regions
        regions_stmt = self._scope_region_query(select(Region.id), user)
        if active_filters.regionId:
            regions_stmt = regions_stmt.where(Region.id == active_filters.regionId)
        regions_count = len(
            (await self.session.execute(regions_stmt)).scalars().all()
        )

        # Today's attendance
        today_start, today_end = self._today_range()
        attendance_base = select(AttendanceRecord).where(
            AttendanceRecord.scannedAt >= today_start,
            AttendanceRecord.scannedAt < today_end,
        )
        att_school_filter = AttendanceRecord.schoolId.in_(scoped_school_ids)
        present_today = await self._scalar_count(
            and_(
                AttendanceRecord.scannedAt >= today_start,
                AttendanceRecord.scannedAt < today_end,
                att_school_filter,
                AttendanceRecord.status == AttendanceStatus.PRESENT,
            ),
            model=AttendanceRecord,
        )
        attendance_today = await self._scalar_count(
            and_(
                AttendanceRecord.scannedAt >= today_start,
                AttendanceRecord.scannedAt < today_end,
                att_school_filter,
            ),
            model=AttendanceRecord,
        )

        # Recent attendance — last 8
        recent_stmt = (
            select(AttendanceRecord)
            .where(
                AttendanceRecord.scannedAt >= today_start,
                AttendanceRecord.scannedAt < today_end,
                att_school_filter,
            )
            .order_by(AttendanceRecord.scannedAt.desc())
            .limit(8)
            .options(
                selectinload(AttendanceRecord.student).selectinload(Student.school),
                selectinload(AttendanceRecord.student).selectinload(Student.classRoom),
                selectinload(AttendanceRecord.student).selectinload(Student.qrCredential),
                selectinload(AttendanceRecord.teacher).selectinload(Teacher.school),
                selectinload(AttendanceRecord.teacher).selectinload(Teacher.qrCredential),
                selectinload(AttendanceRecord.teacher).selectinload(Teacher.classes),
            )
        )
        recent_rows = (await self.session.execute(recent_stmt)).scalars().unique().all()

        # Per-region breakdown (with full School rows for enrichment)
        region_rows_stmt = (
            self._scope_region_query(
                select(Region).options(
                    selectinload(Region.schools).selectinload(School.region)
                ),
                user,
            )
            .order_by(Region.name.asc())
        )
        if active_filters.regionId:
            region_rows_stmt = region_rows_stmt.where(Region.id == active_filters.regionId)
        region_rows = (
            await self.session.execute(region_rows_stmt)
        ).scalars().unique().all()

        # Per-school counts
        school_id_list = list(
            (await self.session.execute(scoped_school_ids)).scalars().all()
        )
        student_per_school = await self._counts_by(
            Student.schoolId, Student, school_id_list
        )
        teacher_per_school = await self._counts_by(
            Teacher.schoolId, Teacher, school_id_list
        )
        class_per_school = await self._counts_by(
            ClassRoom.schoolId, ClassRoom, school_id_list
        )

        # Class rows for capacity / overload
        class_stmt = (
            select(ClassRoom)
            .where(ClassRoom.schoolId.in_(scoped_school_ids))
            .options(selectinload(ClassRoom.school).selectinload(School.region))
        )
        class_rows = (await self.session.execute(class_stmt)).scalars().unique().all()
        class_id_list = [c.id for c in class_rows]
        students_per_class = await self._counts_by(
            Student.classRoomId, Student, class_id_list
        )

        class_capacity = sum(c.maxStudents or 0 for c in class_rows)
        assigned_students = sum(students_per_class.values())
        overloaded = [
            c for c in class_rows
            if c.maxStudents and students_per_class.get(c.id, 0) > c.maxStudents
        ]

        # Quality counters
        async def _count_where(*conds: Any, model: Any = Student) -> int:
            return await self._scalar_count(and_(*conds), model=model)

        students_without_class = await _count_where(
            Student.schoolId.in_(scoped_school_ids), Student.classRoomId.is_(None)
        )
        students_without_photo = await _count_where(
            Student.schoolId.in_(scoped_school_ids), Student.photoUrl.is_(None)
        )
        students_missing_birth = await _count_where(
            Student.schoolId.in_(scoped_school_ids), Student.birthDate.is_(None)
        )
        teachers_without_class_count = await self._scalar_count(
            and_(
                Teacher.schoolId.in_(scoped_school_ids),
                ~Teacher.id.in_(select(class_room_teacher_table.c.B)),
            ),
            model=Teacher,
        )
        teachers_without_photo = await _count_where(
            Teacher.schoolId.in_(scoped_school_ids), Teacher.photoUrl.is_(None),
            model=Teacher,
        )
        teachers_missing_birth = await _count_where(
            Teacher.schoolId.in_(scoped_school_ids), Teacher.birthDate.is_(None),
            model=Teacher,
        )
        schools_without_coordinates = await _count_where(
            School.id.in_(scoped_school_ids),
            or_(School.latitude.is_(None), School.longitude.is_(None)),
            model=School,
        )
        schools_missing_phone = await _count_where(
            School.id.in_(scoped_school_ids), School.phone.is_(None), model=School
        )

        # Build by-region payload
        # Build a flat list of schools per region
        scoped_school_id_set = set(school_id_list)
        schools_by_region_payload = []
        for region in region_rows:
            in_scope = [s for s in region.schools if s.id in scoped_school_id_set]
            schools_by_region_payload.append(
                DashboardByRegion(
                    id=region.id,
                    name=region.name,
                    schools=len(in_scope),
                    students=sum(student_per_school.get(s.id, 0) for s in in_scope),
                    teachers=sum(teacher_per_school.get(s.id, 0) for s in in_scope),
                )
            )

        # Build by-prefecture & by-commune territories
        all_scoped_schools = [s for region in region_rows for s in region.schools
                              if s.id in scoped_school_id_set]
        by_prefecture = self._build_territory_rows(
            all_scoped_schools, "prefecture", "Préfecture non renseignée",
            student_per_school, teacher_per_school, class_per_school,
        )
        by_commune = self._build_territory_rows(
            all_scoped_schools, "commune", "Commune non renseignée",
            student_per_school, teacher_per_school, class_per_school,
        )

        geolocated = sum(
            1 for s in all_scoped_schools
            if s.latitude is not None and s.longitude is not None
        )

        # Top 6 schools by students
        top_schools_sorted = sorted(
            all_scoped_schools,
            key=lambda s: student_per_school.get(s.id, 0),
            reverse=True,
        )[:6]

        # Quality score
        missing = (
            students_without_class
            + students_without_photo
            + students_missing_birth
            + teachers_without_class_count
            + teachers_without_photo
            + teachers_missing_birth
            + schools_without_coordinates
            + schools_missing_phone
        )
        possible = students * 3 + teachers * 3 + schools * 2
        quality_score = (
            max(0, round(((possible - missing) / possible) * 100)) if possible else 100
        )

        ratios = DashboardRatios(
            studentsPerTeacher=self._round_ratio(students, teachers),
            studentsPerSchool=self._round_ratio(students, schools),
            teachersPerSchool=self._round_ratio(teachers, schools),
            averageClassSize=self._round_ratio(assigned_students, classes),
        )
        capacity_payload = DashboardCapacity(
            classCapacity=class_capacity,
            assignedStudents=assigned_students,
            fillRate=round((assigned_students / class_capacity) * 100)
            if class_capacity
            else 0,
            overloadedClasses=len(overloaded),
            studentsWithoutClass=students_without_class,
        )
        territory_payload = DashboardTerritory(
            prefectures=len(by_prefecture),
            communes=len(by_commune),
            geolocatedSchools=geolocated,
            gpsCoverageRate=round((geolocated / schools) * 100) if schools else 0,
        )
        alerts = self._dashboard_alerts(
            schools=schools,
            teachers=teachers,
            students_without_class=students_without_class,
            teachers_without_classes=teachers_without_class_count,
            schools_without_coordinates=schools_without_coordinates,
            schools_missing_phone=schools_missing_phone,
            overloaded_classes=len(overloaded),
            students_per_teacher=ratios.studentsPerTeacher,
        )

        return DashboardResponse(
            totals=DashboardTotals(
                students=students, teachers=teachers, schools=schools,
                classes=classes, regions=regions_count,
                presentToday=present_today, attendanceToday=attendance_today,
                registeredPeople=students + teachers,
            ),
            filters=active_filters,
            byRegion=schools_by_region_payload,
            byPrefecture=by_prefecture,
            byCommune=by_commune,
            ratios=ratios,
            capacity=capacity_payload,
            dataQuality=DashboardDataQuality(
                score=quality_score,
                studentsWithoutClass=students_without_class,
                studentsWithoutPhoto=students_without_photo,
                studentsMissingBirthDate=students_missing_birth,
                teachersWithoutClasses=teachers_without_class_count,
                teachersWithoutPhoto=teachers_without_photo,
                teachersMissingBirthDate=teachers_missing_birth,
                schoolsWithoutCoordinates=schools_without_coordinates,
                schoolsMissingPhone=schools_missing_phone,
            ),
            territory=territory_payload,
            operationalAlerts=alerts,
            topSchools=[
                DashboardTopSchool(
                    id=s.id, name=s.name, code=s.code,
                    region=TerritorialBriefRead(
                        id=s.region.id, name=s.region.name, code=s.region.code,
                    ) if s.region else TerritorialBriefRead(id="", name="", code=""),
                    students=student_per_school.get(s.id, 0),
                    teachers=teacher_per_school.get(s.id, 0),
                    classes=class_per_school.get(s.id, 0),
                )
                for s in top_schools_sorted
            ],
            overloadedClasses=[
                DashboardOverloadedClass(
                    id=c.id, name=c.name, level=c.level,
                    school={"id": c.school.id, "name": c.school.name, "code": c.school.code}
                    if c.school else None,
                    students=students_per_class.get(c.id, 0),
                    maxStudents=c.maxStudents,
                )
                for c in overloaded[:6]
            ],
            recentAttendances=[
                self._map_attendance(record) for record in recent_rows
            ],
        )

    # ==================================================================
    # PRIVATE HELPERS
    # ==================================================================
    def _scope_school_query(self, stmt, user: User):  # type: ignore[no-untyped-def]
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
        return stmt.where(School.id == "__none__")

    def _scope_region_query(self, stmt, user: User):  # type: ignore[no-untyped-def]
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.regionId:
            return stmt.where(Region.id == user.regionId)
        return stmt.where(Region.id == "__none__")

    def _scope_prefecture_query(self, stmt, user: User):  # type: ignore[no-untyped-def]
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(Prefecture.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(Prefecture.id == user.prefectureId)
        return stmt.where(Prefecture.id == "__none__")

    def _scope_sub_prefecture_query(self, stmt, user: User):  # type: ignore[no-untyped-def]
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(SubPrefecture.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(SubPrefecture.prefectureId == user.prefectureId)
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(SubPrefecture.id == user.subPrefectureId)
        return stmt.where(SubPrefecture.id == "__none__")

    def _scope_person_query(self, stmt, user: User, *, model):  # type: ignore[no-untyped-def]
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(model.schoolId.in_(
                select(School.id).where(School.regionId == user.regionId)
            ))
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(model.schoolId.in_(
                select(School.id).where(School.prefectureId == user.prefectureId)
            ))
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(model.schoolId.in_(
                select(School.id).where(School.subPrefectureId == user.subPrefectureId)
            ))
        if user.schoolId:
            return stmt.where(model.schoolId == user.schoolId)
        return stmt.where(model.id == "__none__")

    async def _assert_can_access_school(self, user: User, school_id: str) -> None:
        school = await self.session.get(School, school_id)
        if school is None:
            raise NotFoundError(detail="École introuvable")
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId == school.regionId:
            return
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId == school.prefectureId:
            return
        if (
            user.role in SUB_PREFECTURE_SCOPE_ROLES
            and user.subPrefectureId == school.subPrefectureId
        ):
            return
        if user.schoolId == school.id:
            return
        raise ForbiddenError(detail="Accès non autorisé pour cette école")

    async def _assert_class_belongs_to_school(self, class_id: str, school_id: str) -> None:
        existing = (
            await self.session.execute(
                select(ClassRoom.id).where(
                    and_(ClassRoom.id == class_id, ClassRoom.schoolId == school_id)
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise NotFoundError(detail="Classe introuvable pour cette école")

    async def _assert_classes_belong_to_school(
        self, class_ids: list[str], school_id: str
    ) -> None:
        unique_ids = list({cid for cid in class_ids if cid})
        if not unique_ids:
            return
        count = (
            await self.session.execute(
                select(func.count())
                .select_from(ClassRoom)
                .where(ClassRoom.id.in_(unique_ids), ClassRoom.schoolId == school_id)
            )
        ).scalar_one()
        if count != len(unique_ids):
            raise NotFoundError(
                detail="Une ou plusieurs classes sont introuvables pour cette école"
            )

    async def _assert_no_duplicate_student(self, dto: CreateStudentRequest) -> None:
        if not dto.birthDate:
            return
        start = datetime.combine(dto.birthDate, datetime.min.time(), tzinfo=UTC)
        end = start + timedelta(days=1)
        stmt = select(Student.id, Student.uniqueCode, Student.firstName, Student.lastName).where(
            and_(
                Student.schoolId == dto.schoolId,
                func.lower(Student.firstName) == dto.firstName.strip().lower(),
                func.lower(Student.lastName) == dto.lastName.strip().lower(),
                Student.birthDate >= start,
                Student.birthDate < end,
            )
        )
        row = (await self.session.execute(stmt)).first()
        if row is not None:
            raise ConflictError(
                detail=(
                    f"Doublon détecté : {row.firstName} {row.lastName} est déjà "
                    f"enregistré ({row.uniqueCode})."
                )
            )

    async def _assert_no_duplicate_teacher(self, dto: CreateTeacherRequest) -> None:
        if not dto.birthDate:
            return
        start = datetime.combine(dto.birthDate, datetime.min.time(), tzinfo=UTC)
        end = start + timedelta(days=1)
        stmt = select(Teacher.id, Teacher.uniqueCode, Teacher.firstName, Teacher.lastName).where(
            and_(
                Teacher.schoolId == dto.schoolId,
                func.lower(Teacher.firstName) == dto.firstName.strip().lower(),
                func.lower(Teacher.lastName) == dto.lastName.strip().lower(),
                Teacher.birthDate >= start,
                Teacher.birthDate < end,
            )
        )
        row = (await self.session.execute(stmt)).first()
        if row is not None:
            raise ConflictError(
                detail=(
                    f"Doublon détecté : {row.firstName} {row.lastName} est déjà "
                    f"enregistré ({row.uniqueCode})."
                )
            )

    async def _generate_unique_code(
        self, person_type: PersonType, school_id: str
    ) -> str:
        stmt = select(School).where(School.id == school_id).options(selectinload(School.region))
        school = (await self.session.execute(stmt)).scalar_one()
        segment = "ELV" if person_type == PersonType.STUDENT else "ENS"
        year = datetime.now(UTC).year
        prefix = f"{school.region.code}-{school.code}-{segment}-{year}"

        if person_type == PersonType.STUDENT:
            sequence = await self._scalar_count(Student.schoolId == school_id) + 1
            while True:
                candidate = f"{prefix}-{sequence:06d}"
                exists = (
                    await self.session.execute(
                        select(Student.id).where(Student.uniqueCode == candidate)
                    )
                ).scalar_one_or_none()
                if exists is None:
                    return candidate
                sequence += 1

        sequence = await self._scalar_count(
            Teacher.schoolId == school_id, model=Teacher
        ) + 1
        while True:
            candidate = f"{prefix}-{sequence:06d}"
            exists = (
                await self.session.execute(
                    select(Teacher.id).where(Teacher.uniqueCode == candidate)
                )
            ).scalar_one_or_none()
            if exists is None:
                return candidate
            sequence += 1

    def _teacher_review_target(
        self, user: User, school: School
    ) -> dict[str, Any] | None:
        if user.role == UserRole.SUB_PREFECTURE_ADMIN and school.prefectureId:
            return {"role": UserRole.PREFECTURE_ADMIN, "prefectureId": school.prefectureId}
        if (
            user.role in (UserRole.SCHOOL_DIRECTOR, UserRole.CENSUS_AGENT)
            and school.subPrefectureId
        ):
            return {
                "role": UserRole.SUB_PREFECTURE_ADMIN,
                "subPrefectureId": school.subPrefectureId,
            }
        if (
            user.role in (UserRole.SCHOOL_DIRECTOR, UserRole.CENSUS_AGENT)
            and school.prefectureId
        ):
            return {"role": UserRole.PREFECTURE_ADMIN, "prefectureId": school.prefectureId}
        return None

    async def _scalar_count(self, condition: Any, *, model: Any = Student) -> int:
        return (
            await self.session.execute(
                select(func.count()).select_from(model).where(condition)
            )
        ).scalar_one()

    async def _counts_by(self, group_col: Any, model: Any, ids: list[str]) -> dict[str, int]:
        if not ids:
            return {}
        rows = (
            await self.session.execute(
                select(group_col, func.count()).where(group_col.in_(ids)).group_by(group_col)
            )
        ).all()
        return dict(rows)

    @staticmethod
    def _today_range() -> tuple[datetime, datetime]:
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)

    @staticmethod
    def _round_ratio(numerator: int, denominator: int) -> float:
        if not denominator:
            return 0.0
        return round((numerator / denominator) * 10) / 10

    @staticmethod
    def _build_territory_rows(
        schools: list[School],
        field: str,
        fallback: str,
        student_counts: dict[str, int],
        teacher_counts: dict[str, int],
        class_counts: dict[str, int],
    ) -> list[DashboardByTerritory]:
        rows: dict[str, dict[str, Any]] = {}
        for school in schools:
            name = getattr(school, field) or fallback
            region = school.region
            if not region:
                continue
            key = f"{region.id}:{name}"
            current = rows.setdefault(
                key,
                {
                    "id": key,
                    "name": name,
                    "region": TerritorialBriefRead(
                        id=region.id, name=region.name, code=region.code
                    ),
                    "schools": 0,
                    "students": 0,
                    "teachers": 0,
                    "classes": 0,
                    "geolocated": 0,
                },
            )
            current["schools"] += 1
            current["students"] += student_counts.get(school.id, 0)
            current["teachers"] += teacher_counts.get(school.id, 0)
            current["classes"] += class_counts.get(school.id, 0)
            if school.latitude is not None and school.longitude is not None:
                current["geolocated"] += 1

        result: list[DashboardByTerritory] = []
        for v in rows.values():
            ratio = round((v["students"] / v["teachers"]) * 10) / 10 if v["teachers"] else 0.0
            gps = round((v["geolocated"] / v["schools"]) * 100) if v["schools"] else 0
            result.append(DashboardByTerritory(
                id=v["id"], name=v["name"], region=v["region"],
                schools=v["schools"], students=v["students"], teachers=v["teachers"],
                classes=v["classes"], geolocatedSchools=v["geolocated"],
                studentsPerTeacher=ratio, gpsCoverageRate=gps,
            ))
        result.sort(key=lambda r: r.students, reverse=True)
        return result[:10]

    @staticmethod
    def _dashboard_alerts(
        *, schools: int, teachers: int,
        students_without_class: int, teachers_without_classes: int,
        schools_without_coordinates: int, schools_missing_phone: int,
        overloaded_classes: int, students_per_teacher: float,
    ) -> list[DashboardAlert]:
        alerts: list[DashboardAlert] = []

        if not schools:
            alerts.append(DashboardAlert(
                level="danger", title="Aucune école enregistrée",
                description="Le périmètre actif ne contient pas encore d'établissement.",
            ))
        if not teachers:
            alerts.append(DashboardAlert(
                level="danger", title="Aucun enseignant recensé",
                description="Le ratio élèves/enseignant ne peut pas encore être calculé.",
            ))
        elif students_per_teacher > 60:
            alerts.append(DashboardAlert(
                level="warning", title="Ratio élèves/enseignant élevé",
                description=f"{students_per_teacher} élèves par enseignant dans le périmètre actif.",
            ))
        if students_without_class:
            alerts.append(DashboardAlert(
                level="warning", title="Élèves sans classe",
                description=f"{students_without_class} dossier(s) élève doivent être affectés à une classe.",
            ))
        if teachers_without_classes:
            alerts.append(DashboardAlert(
                level="warning", title="Enseignants sans classe",
                description=f"{teachers_without_classes} enseignant(s) doivent être rattachés à au moins une classe.",
            ))
        if overloaded_classes:
            alerts.append(DashboardAlert(
                level="danger", title="Classes surchargées",
                description=f"{overloaded_classes} classe(s) dépassent leur capacité déclarée.",
            ))
        if schools_without_coordinates:
            alerts.append(DashboardAlert(
                level="info", title="Géolocalisation incomplète",
                description=f"{schools_without_coordinates} école(s) doivent recevoir des coordonnées GPS.",
            ))
        if schools_missing_phone:
            alerts.append(DashboardAlert(
                level="info", title="Contacts établissements incomplets",
                description=f"{schools_missing_phone} école(s) n'ont pas encore de téléphone administratif.",
            ))

        if not alerts:
            return [DashboardAlert(
                level="success", title="Aucune alerte critique",
                description="Les données prioritaires du périmètre actif sont cohérentes.",
            )]
        return alerts[:5]

    # --- Mapping helpers ----------------------------------------------
    @staticmethod
    def _map_student(student: Student) -> StudentRead:
        school_payload = (
            SchoolEmbedded.model_validate(student.school) if student.school else None
        )
        class_payload: ClassRoomSummary | None = None
        if student.classRoom:
            class_payload = ClassRoomSummary(
                id=student.classRoom.id,
                name=student.classRoom.name,
                level=student.classRoom.level,
                maxStudents=student.classRoom.maxStudents,
                schoolYear=student.classRoom.schoolYear,
                schoolId=student.classRoom.schoolId,
                school=None,  # avoid recursive load
                createdAt=student.classRoom.createdAt,
                updatedAt=student.classRoom.updatedAt,
            )

        transfers: list[TransferHistoryItem] | None = None
        try:
            history = list(student.transferHistory)  # may raise if not loaded
            transfers = [
                TransferHistoryItem(
                    id=t.id,
                    transferredAt=t.transferredAt,
                    reason=t.reason,
                    fromSchool=SchoolEmbedded.model_validate(t.fromSchool)
                    if t.fromSchool else None,
                    toSchool=SchoolEmbedded.model_validate(t.toSchool)
                    if t.toSchool else None,
                    fromClassRoom=None,  # simplified; full mapping deferred
                    toClassRoom=None,
                    actor={
                        "id": t.actor.id, "fullName": t.actor.fullName, "email": t.actor.email,
                    } if t.actor else None,
                )
                for t in history
            ]
        except Exception:
            transfers = None

        return StudentRead(
            id=student.id,
            uniqueCode=student.uniqueCode,
            firstName=student.firstName,
            lastName=student.lastName,
            fullName=f"{student.firstName} {student.lastName}",
            gender=student.gender,
            birthDate=student.birthDate,
            photoUrl=student.photoUrl,
            guardianName=student.guardianName,
            guardianPhone=student.guardianPhone,
            school=school_payload,
            classRoom=class_payload,
            transferHistory=transfers,
            qrToken=student.qrCredential.token if student.qrCredential else None,
            qrPayload=student.uniqueCode,
            qrSvg=None,  # Phase 5
            createdAt=student.createdAt,
        )

    @staticmethod
    def _map_teacher(teacher: Teacher) -> TeacherRead:
        school_payload = (
            SchoolEmbedded.model_validate(teacher.school) if teacher.school else None
        )
        classes_payload: list[ClassRoomSummary] = []
        try:
            for c in list(teacher.classes):
                classes_payload.append(
                    ClassRoomSummary(
                        id=c.id, name=c.name, level=c.level,
                        maxStudents=c.maxStudents, schoolYear=c.schoolYear,
                        schoolId=c.schoolId,
                        school=SchoolEmbedded.model_validate(c.school) if c.school else None,
                        createdAt=c.createdAt, updatedAt=c.updatedAt,
                    )
                )
        except Exception:
            classes_payload = []

        return TeacherRead(
            id=teacher.id,
            uniqueCode=teacher.uniqueCode,
            firstName=teacher.firstName,
            lastName=teacher.lastName,
            fullName=f"{teacher.firstName} {teacher.lastName}",
            gender=teacher.gender,
            birthDate=teacher.birthDate,
            photoUrl=teacher.photoUrl,
            phone=teacher.phone,
            subject=teacher.subject,
            diploma=teacher.diploma,
            status=teacher.status,
            rejectionReason=teacher.rejectionReason,
            school=school_payload,
            classes=classes_payload,
            qrToken=teacher.qrCredential.token if teacher.qrCredential else None,
            qrPayload=teacher.uniqueCode,
            qrSvg=None,  # Phase 5
            createdAt=teacher.createdAt,
        )

    @classmethod
    def _map_attendance(cls, record: AttendanceRecord) -> RecentAttendance:
        person: StudentRead | TeacherRead | None
        if record.personType == PersonType.STUDENT and record.student:
            person = cls._map_student(record.student)
        elif record.personType == PersonType.TEACHER and record.teacher:
            person = cls._map_teacher(record.teacher)
        else:
            person = None
        return RecentAttendance(
            id=record.id, personType=record.personType, status=record.status,
            scannedAt=record.scannedAt, person=person,
        )
