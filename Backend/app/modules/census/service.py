"""Census service — students, teachers, dashboard, metadata.

Notes
-----
* QR SVG rendering is intentionally deferred to Phase 5 (attendance scan).
  Student/Teacher creation still generates a QrCredential row + token, but
  responses return ``qrSvg=None``. The /api/census/identify and /api/census/qr
  endpoints will be implemented alongside the attendance flow.
* All write operations record an AuditLog row matching the NestJS contract.
"""
import unicodedata
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from uuid import uuid4

import qrcode
import qrcode.image.svg
from sqlalchemy import and_, delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from app.core.observability import (
    census_duplicate_blocked_total,
    census_duplicate_check_total,
    census_merge_total,
)
from app.modules.academics.models import (
    Grade,
    ParentCommunication,
    ReportCard,
    StudentParent,
)
from app.modules.attendance.models import AttendanceRecord, QrCredential
from app.modules.auth.models import User
from app.modules.census.duplicates import (
    classify_score,
    compute_similarity_score,
    force_classification_floor,
)
from app.modules.census.models import Student, StudentTransfer, Teacher
from app.modules.census.normalization import validate_birthdate_for_classroom
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
    StudentDuplicateCheckRequest,
    StudentDuplicateCheckResponse,
    StudentDuplicateMatch,
    StudentRead,
    TeacherDuplicateCheckRequest,
    TeacherDuplicateCheckResponse,
    TeacherDuplicateMatch,
    TeacherRead,
    TransferHistoryItem,
    TransferStudentRequest,
)
from app.modules.library.models import LibraryLoan
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

# Roles autorisés à fusionner deux fiches élèves (administration territoriale).
MERGE_STUDENTS_ROLES: frozenset[UserRole] = frozenset(
    {
        UserRole.NATIONAL_ADMIN,
        UserRole.MINISTRY_ADMIN,
        UserRole.REGIONAL_ADMIN,
        UserRole.PREFECTURE_ADMIN,
    }
)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _ascii_fold(value: str) -> str:
    """Replie ``value`` en ASCII (NFKD + drop des combining marks).

    Sert à stocker des chaînes diacritiques dans une DB encodée SQL_ASCII
    (cf. AuditLog metadata) sans perdre le sens lisible. Les chaînes déjà
    ASCII passent inchangées. À ne PAS utiliser pour des données critiques
    (noms d'élèves, communications parents) — uniquement pour des traces.
    """
    if not value:
        return value
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


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

    async def create_student(
        self,
        user: User,
        dto: CreateStudentRequest,
        *,
        force: bool = False,
    ) -> StudentRead:
        await self._assert_can_access_school(user, dto.schoolId)

        # C-1 review Module 2 — dernière barrière EXACT-MATCH (legacy) :
        # si même (lastName, firstName, birthDate) dans la même école, on
        # bloque même si le scoring fuzzy n'a rien proposé. Ce check
        # prend la précédence pour produire un message d'erreur explicite.
        # Avec ``force=true``, l'agent peut quand même créer la fiche
        # (même politique que pour les HIGH fuzzy) — la trace audit
        # ci-dessous distingue les deux cas.
        if not force:
            await self._assert_no_duplicate_student(dto)

        # Module 2 — vérification fuzzy. Si un doublon HIGH existe ET que
        # l'agent n'a pas explicitement fourni force=true, on bloque avec
        # 409 + payload listant les candidats. La barrière exact-match
        # ci-dessus traite déjà le cas extrême ; ici on rattrape les
        # variantes orthographiques (Aichatou/Aïssatou, Dialo/Diallo, ...).
        fuzzy_matches = await self._scan_student_duplicates(dto)
        high_matches = [m for m in fuzzy_matches if m.classification == "HIGH"]
        if high_matches and not force:
            census_duplicate_blocked_total.labels(
                entity="student", level="HIGH"
            ).inc()
            raise ConflictError(
                detail="Doublon potentiel détecté",
                extra={
                    "duplicates": [m.model_dump(mode="json") for m in high_matches],
                },
            )

        if dto.classRoomId:
            await self._assert_class_belongs_to_school(dto.classRoomId, dto.schoolId)

        # M-6 review Module 2 — cohérence âge/niveau. La feature existait
        # depuis Module 2 mais n'était jamais appelée. On la branche ici
        # après le check classroom→école pour pouvoir lire ``level``.
        await self._assert_birthdate_matches_classroom(
            dto, user=user, force=force,
        )

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
        audit_metadata: dict[str, Any] = {"uniqueCode": unique_code}
        if force and high_matches:
            # Trace de l'override pour audit + analyse a posteriori.
            audit_metadata["reason"] = "force_creation_after_duplicate_warning"
            audit_metadata["forcedDuplicates"] = [
                {"id": m.id, "score": m.score, "classification": m.classification}
                for m in high_matches
            ]
        self.session.add(
            AuditLog(
                actorId=user.id,
                action="CREATE_STUDENT",
                entity="Student",
                entityId=student.id,
                metadata_=audit_metadata,
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

    async def create_teacher(
        self,
        user: User,
        dto: CreateTeacherRequest,
        *,
        force: bool = False,
    ) -> TeacherRead:
        await self._assert_can_access_school(user, dto.schoolId)

        # C-1 review Module 2 — dernière barrière EXACT-MATCH (legacy).
        # Cf. create_student pour la rationale.
        if not force:
            await self._assert_no_duplicate_teacher(dto)

        # Module 2 — vérification fuzzy enseignants (signature plus simple :
        # lastName + firstName + birthDate). Pas de guardianPhone côté teacher.
        teacher_matches = await self._scan_teacher_duplicates(dto)
        high_t_matches = [m for m in teacher_matches if m["classification"] == "HIGH"]
        if high_t_matches and not force:
            census_duplicate_blocked_total.labels(
                entity="teacher", level="HIGH"
            ).inc()
            raise ConflictError(
                detail="Doublon potentiel détecté",
                extra={"duplicates": high_t_matches},
            )

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
        teacher_audit_meta: dict[str, Any] = {"uniqueCode": unique_code}
        if force and high_t_matches:
            teacher_audit_meta["reason"] = "force_creation_after_duplicate_warning"
            teacher_audit_meta["forcedDuplicates"] = [
                {
                    "id": m["id"],
                    "score": m["score"],
                    "classification": m["classification"],
                }
                for m in high_t_matches
            ]
        self.session.add(
            AuditLog(
                actorId=user.id,
                action="CREATE_TEACHER",
                entity="Teacher",
                entityId=teacher.id,
                metadata_=teacher_audit_meta,
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
    # DUPLICATES (Module 2)
    # ==================================================================
    async def check_student_duplicates(
        self, user: User, dto: StudentDuplicateCheckRequest
    ) -> StudentDuplicateCheckResponse:
        """Liste les fiches élèves les plus proches du DTO via pg_trgm + scoring.

        RBAC : le scope territorial de l'utilisateur est appliqué — un
        SCHOOL_DIRECTOR ne voit que son école, un REGIONAL_ADMIN sa région.
        """
        census_duplicate_check_total.labels(entity="student").inc()
        matches = await self._scan_student_duplicates(
            dto, user_scope=user, limit_candidates=20
        )
        return StudentDuplicateCheckResponse(matches=matches[:5], total=len(matches))

    async def check_teacher_duplicates(
        self, user: User, dto: TeacherDuplicateCheckRequest
    ) -> TeacherDuplicateCheckResponse:
        """Pendant du check-duplicates côté enseignants (C-3 review Module 2).

        Avant : aucun endpoint exposé, le scan teacher n'était accessible
        qu'indirectement via create_teacher. Symétrie minimale pour l'UI
        d'aide à la saisie (lookup avant création).
        """
        census_duplicate_check_total.labels(entity="teacher").inc()
        first = (dto.firstName or "").strip().lower()
        last = (dto.lastName or "").strip().lower()
        if not first or not last:
            return TeacherDuplicateCheckResponse(matches=[], total=0)

        stmt = (
            select(Teacher)
            .where(func.similarity(func.lower(Teacher.lastName), last) > 0.3)
            .order_by(func.similarity(func.lower(Teacher.lastName), last).desc())
            .limit(20)
        )
        if dto.schoolId:
            stmt = stmt.where(Teacher.schoolId == dto.schoolId)
        stmt = self._scope_person_query(stmt, user, model=Teacher)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        candidate = {
            "firstName": dto.firstName,
            "lastName": dto.lastName,
            "birthDate": dto.birthDate,
            "gender": dto.gender,
            "schoolId": dto.schoolId,
        }
        matches: list[TeacherDuplicateMatch] = []
        for teacher in rows:
            row_payload = {
                "firstName": teacher.firstName,
                "lastName": teacher.lastName,
                "birthDate": teacher.birthDate,
                "gender": teacher.gender,
                "schoolId": teacher.schoolId,
            }
            scored = compute_similarity_score(candidate, row_payload)
            classification = classify_score(scored["score"])
            classification = force_classification_floor(
                candidate, row_payload, classification,
            )
            if classification == "LOW":
                continue
            birth_year = teacher.birthDate.year if teacher.birthDate else None
            birth_matches: bool | None
            if teacher.birthDate is None or dto.birthDate is None:
                birth_matches = None
            else:
                row_d = (
                    teacher.birthDate.date()
                    if isinstance(teacher.birthDate, datetime)
                    else teacher.birthDate
                )
                birth_matches = row_d == dto.birthDate
            matches.append(
                TeacherDuplicateMatch(
                    id=teacher.id,
                    firstName=teacher.firstName,
                    lastName=teacher.lastName,
                    birthYear=birth_year,
                    birthDateMatches=birth_matches,
                    schoolId=teacher.schoolId,
                    score=scored["score"],
                    classification=classification,  # type: ignore[arg-type]
                    matchedFields=scored["matchedFields"],
                )
            )
        matches.sort(key=lambda m: m.score, reverse=True)
        return TeacherDuplicateCheckResponse(matches=matches[:5], total=len(matches))

    async def merge_students(
        self,
        user: User,
        source_id: str,
        target_id: str,
        *,
        reason: str | None = None,
    ) -> StudentRead:
        """Fusionne ``source_id`` dans ``target_id``.

        * Vérifie source ≠ target, RBAC, accès aux écoles concernées.
        * Déplace les rows dépendantes (Grade, ReportCard, AttendanceRecord,
          LibraryLoan, ParentCommunication, StudentParent, StudentTransfer)
          de source vers target en une seule transaction.
        * Supprime QrCredential du source puis le Student lui-même.
        * Écrit un AuditLog avec le détail des transferts.
        * Idempotent : si ``source_id`` est déjà introuvable, on retourne le
          target (404 propre sur target seulement).
        """
        if source_id == target_id:
            raise ConflictError(detail="Impossible de fusionner un élève avec lui-même")

        if user.role not in MERGE_STUDENTS_ROLES:
            census_merge_total.labels(entity="student", result="forbidden").inc()
            raise ForbiddenError(
                detail="Seul un administrateur territorial peut fusionner deux fiches élève",
                extra={"required_any_of": sorted(r.value for r in MERGE_STUDENTS_ROLES)},
            )

        target = await self.session.get(Student, target_id)
        if target is None:
            census_merge_total.labels(entity="student", result="not_found").inc()
            raise NotFoundError(detail="Élève cible introuvable")
        await self._assert_can_access_school(user, target.schoolId)

        source = await self.session.get(Student, source_id)
        if source is None:
            # Idempotence : le source a peut-être déjà été fusionné. On
            # retourne le target tel quel pour permettre au client de rejouer.
            census_merge_total.labels(entity="student", result="ok").inc()
            return await self.get_student(user, target_id)

        await self._assert_can_access_school(user, source.schoolId)

        transferred = await self._transfer_student_dependents(source_id, target_id)

        # Cleanup : QrCredential du source ne doit plus pointer vers lui.
        await self.session.execute(
            delete(QrCredential).where(QrCredential.studentId == source_id)
        )
        # Et suppression du Student source.
        await self.session.execute(delete(Student).where(Student.id == source_id))

        merge_metadata: dict[str, Any] = {
            "source_id": source_id,
            "target_id": target_id,
            "transferred": transferred,
        }
        if reason:
            merge_metadata["reason"] = reason
        self.session.add(
            AuditLog(
                actorId=user.id,
                action="MERGE_STUDENTS",
                entity="Student",
                entityId=target_id,
                metadata_=merge_metadata,
            )
        )
        await self.session.flush()
        census_merge_total.labels(entity="student", result="ok").inc()
        return await self.get_student(user, target_id)

    async def _transfer_student_dependents(
        self, source_id: str, target_id: str
    ) -> dict[str, int]:
        """Bascule toutes les rows dépendantes de ``source_id`` vers ``target_id``.

        Retourne un dict ``{table: rowcount, ...}`` pour l'audit.
        """
        counts: dict[str, int] = {}

        async def _bulk_update(model: Any, name: str) -> None:
            res = await self.session.execute(
                update(model)
                .where(model.studentId == source_id)
                .values(studentId=target_id)
            )
            counts[name] = res.rowcount or 0

        # Grades : contrainte unique (assessmentId, studentId) — on
        # nettoie d'abord les conflits éventuels (le target gagne).
        conflict_grades = await self.session.execute(
            select(Grade.assessmentId)
            .where(Grade.studentId == target_id)
        )
        target_assessment_ids = set(conflict_grades.scalars().all())
        if target_assessment_ids:
            await self.session.execute(
                delete(Grade).where(
                    Grade.studentId == source_id,
                    Grade.assessmentId.in_(target_assessment_ids),
                )
            )
        await _bulk_update(Grade, "grades")

        # ReportCard : unique (studentId, periodId) — même politique.
        conflict_rc = await self.session.execute(
            select(ReportCard.periodId).where(ReportCard.studentId == target_id)
        )
        target_period_ids = set(conflict_rc.scalars().all())
        if target_period_ids:
            await self.session.execute(
                delete(ReportCard).where(
                    ReportCard.studentId == source_id,
                    ReportCard.periodId.in_(target_period_ids),
                )
            )
        await _bulk_update(ReportCard, "reportCards")

        await _bulk_update(AttendanceRecord, "attendances")
        await _bulk_update(LibraryLoan, "libraryLoans")
        await _bulk_update(ParentCommunication, "parentCommunications")

        # StudentParent : unique (studentId, parentId, relation) — on
        # supprime d'abord les liens du source qui dupliqueraient ceux du
        # target, puis on bascule le reste.
        conflict_sp = await self.session.execute(
            select(StudentParent.parentId, StudentParent.relation)
            .where(StudentParent.studentId == target_id)
        )
        target_parent_relations = {tuple(row) for row in conflict_sp.all()}
        if target_parent_relations:
            for parent_id, relation in target_parent_relations:
                await self.session.execute(
                    delete(StudentParent).where(
                        StudentParent.studentId == source_id,
                        StudentParent.parentId == parent_id,
                        StudentParent.relation == relation,
                    )
                )
        await _bulk_update(StudentParent, "studentParents")
        await _bulk_update(StudentTransfer, "transfers")
        await self.session.flush()
        return counts

    async def _scan_student_duplicates(
        self,
        dto: CreateStudentRequest | StudentDuplicateCheckRequest,
        *,
        user_scope: User | None = None,
        limit_candidates: int = 20,
    ) -> list[StudentDuplicateMatch]:
        """Trouve les candidats doublons via pg_trgm + scoring fuzzy.

        Si ``user_scope`` est fourni, le scope territorial du user est
        appliqué (utile pour /check-duplicates où on veut respecter le RBAC).
        Pour la création (create_student), on ne scope PAS — un agent qui
        crée un élève dans une école doit voir les doublons dans cette école
        (et seulement celle-ci) via le filtre schoolId du DTO.
        """
        first = (dto.firstName or "").strip().lower()
        last = (dto.lastName or "").strip().lower()
        if not first or not last:
            return []

        # Query principale : on filtre via la fonction similarity() de pg_trgm
        # sur le lastName (signal le plus fort), seuil bas (0.3) pour
        # rattraper les variantes orthographiques courantes. Le scoring fin
        # est fait Python-side juste après.
        stmt = (
            select(Student)
            .where(
                func.similarity(func.lower(Student.lastName), last) > 0.3,
            )
            .options(selectinload(Student.school))
            .order_by(
                func.similarity(func.lower(Student.lastName), last).desc()
            )
            .limit(limit_candidates)
        )
        if isinstance(dto, CreateStudentRequest):
            # Pour la création, scope au schoolId du DTO (un agent ne
            # crée pas un élève dans une autre école sans le savoir).
            stmt = stmt.where(Student.schoolId == dto.schoolId)
        elif user_scope is not None:
            stmt = self._scope_person_query(stmt, user_scope, model=Student)

        rows = (await self.session.execute(stmt)).scalars().unique().all()
        candidate_payload = {
            "firstName": dto.firstName,
            "lastName": dto.lastName,
            "birthDate": dto.birthDate,
            "guardianPhone": getattr(dto, "guardianPhone", None),
            "gender": getattr(dto, "gender", None),
            "schoolId": getattr(dto, "schoolId", None),
        }

        matches: list[StudentDuplicateMatch] = []
        for student in rows:
            row_payload = {
                "firstName": student.firstName,
                "lastName": student.lastName,
                "birthDate": student.birthDate,
                "guardianPhone": student.guardianPhone,
                "gender": student.gender,
                "schoolId": student.schoolId,
            }
            scored = compute_similarity_score(candidate_payload, row_payload)
            classification = classify_score(scored["score"])
            # Fallback exact-match (C-1 review) : si le scoring laisse passer
            # un appariement noms+école+genre très fort, on remonte le niveau.
            classification = force_classification_floor(
                candidate_payload, row_payload, classification,
            )
            if classification == "LOW":
                continue
            school_name = student.school.name if student.school else None
            matches.append(
                self._build_student_match(
                    student,
                    school_name=school_name,
                    score=scored["score"],
                    classification=classification,
                    matched_fields=scored["matchedFields"],
                    candidate_birth=candidate_payload.get("birthDate"),
                )
            )

        matches.sort(key=lambda m: m.score, reverse=True)
        return matches

    @staticmethod
    def _build_student_match(
        student: Student,
        *,
        school_name: str | None,
        score: float,
        classification: str,
        matched_fields: list[str],
        candidate_birth: Any,
    ) -> StudentDuplicateMatch:
        """Construit un StudentDuplicateMatch en masquant la birthDate exacte.

        Conformément à C-3 (review Module 2) : on ne renvoie PAS la birthDate
        complète (énumération sensible), uniquement l'année + un flag de
        correspondance avec l'input.
        """
        birth_year: int | None = None
        if student.birthDate is not None:
            birth_year = student.birthDate.year

        birth_matches: bool | None
        if student.birthDate is None or candidate_birth is None:
            birth_matches = None
        else:
            row_d = (
                student.birthDate.date()
                if isinstance(student.birthDate, datetime)
                else student.birthDate
            )
            cand_d = (
                candidate_birth.date()
                if isinstance(candidate_birth, datetime)
                else candidate_birth
            )
            birth_matches = row_d == cand_d

        return StudentDuplicateMatch(
            id=student.id,
            firstName=student.firstName,
            lastName=student.lastName,
            birthYear=birth_year,
            birthDateMatches=birth_matches,
            schoolId=student.schoolId,
            schoolName=school_name,
            score=score,
            classification=classification,  # type: ignore[arg-type]
            matchedFields=matched_fields,
        )

    async def _scan_teacher_duplicates(
        self, dto: CreateTeacherRequest
    ) -> list[dict[str, Any]]:
        """Scan minimaliste des doublons enseignants (lastName + firstName + birthDate)."""
        first = (dto.firstName or "").strip().lower()
        last = (dto.lastName or "").strip().lower()
        if not first or not last:
            return []

        stmt = (
            select(Teacher)
            .where(
                Teacher.schoolId == dto.schoolId,
                func.similarity(func.lower(Teacher.lastName), last) > 0.3,
            )
            .order_by(
                func.similarity(func.lower(Teacher.lastName), last).desc()
            )
            .limit(20)
        )
        rows = (await self.session.execute(stmt)).scalars().unique().all()

        candidate = {
            "firstName": dto.firstName,
            "lastName": dto.lastName,
            "birthDate": dto.birthDate,
            "gender": dto.gender,
            "schoolId": dto.schoolId,
        }
        matches: list[dict[str, Any]] = []
        for teacher in rows:
            row_payload = {
                "firstName": teacher.firstName,
                "lastName": teacher.lastName,
                "birthDate": teacher.birthDate,
                "gender": teacher.gender,
                "schoolId": teacher.schoolId,
            }
            scored = compute_similarity_score(candidate, row_payload)
            classification = classify_score(scored["score"])
            classification = force_classification_floor(
                candidate, row_payload, classification,
            )
            if classification == "LOW":
                continue
            # Idem C-3 : on ne renvoie pas la birthDate exacte (énumération).
            birth_year = teacher.birthDate.year if teacher.birthDate else None
            birth_matches: bool | None
            if teacher.birthDate is None or dto.birthDate is None:
                birth_matches = None
            else:
                row_d = (
                    teacher.birthDate.date()
                    if isinstance(teacher.birthDate, datetime)
                    else teacher.birthDate
                )
                birth_matches = row_d == dto.birthDate
            matches.append({
                "id": teacher.id,
                "firstName": teacher.firstName,
                "lastName": teacher.lastName,
                "schoolId": teacher.schoolId,
                "birthYear": birth_year,
                "birthDateMatches": birth_matches,
                "score": scored["score"],
                "classification": classification,
                "matchedFields": scored["matchedFields"],
            })
        matches.sort(key=lambda m: m["score"], reverse=True)
        return matches

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

    async def _assert_birthdate_matches_classroom(
        self,
        dto: CreateStudentRequest,
        *,
        user: User,
        force: bool,
    ) -> None:
        """M-6 review Module 2 — cohérence âge ↔ niveau de la classe.

        Si la classe a un ``level`` connu (CP, CE2, CM1, ...) et que le DTO
        porte une ``birthDate``, on appelle ``validate_birthdate_for_classroom``.
        Sur incohérence :

        * ``force=False`` → 422 ``ValidationFailedError`` (l'agent doit
          confirmer explicitement).
        * ``force=True``  → on autorise mais on trace un AuditLog dédié.

        Sans birthDate ou sans classroom ou sans level, on ne valide rien
        (chaque champ est facultatif côté schéma).
        """
        if dto.birthDate is None or not dto.classRoomId:
            return
        classroom = await self.session.get(ClassRoom, dto.classRoomId)
        if classroom is None or not classroom.level:
            return
        ok, reason = validate_birthdate_for_classroom(dto.birthDate, classroom.level)
        if ok:
            return
        if not force:
            raise ValidationFailedError(
                detail=f"Date de naissance incohérente avec la classe: {reason}",
                extra={
                    "classRoomId": dto.classRoomId,
                    "level": classroom.level,
                    "reason": reason,
                },
            )
        # force=True : on autorise mais on trace pour audit a posteriori.
        # NB : on ASCII-fold ``reason`` pour rester compatible avec une DB
        # encodée en SQL_ASCII (chaîne d'origine restituée côté UI/i18n).
        self.session.add(
            AuditLog(
                actorId=user.id,
                action="OVERRIDE_BIRTHDATE_INCONSISTENT_WITH_CLASSROOM",
                entity="Student",
                entityId=None,
                metadata_={
                    "classRoomId": dto.classRoomId,
                    "level": classroom.level,
                    "birthDate": dto.birthDate.isoformat(),
                    "reason": _ascii_fold(reason or ""),
                },
            )
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
            census_duplicate_blocked_total.labels(
                entity="student", level="EXACT"
            ).inc()
            raise ConflictError(
                detail=(
                    f"Doublon détecté : {row.firstName} {row.lastName} est déjà "
                    f"enregistré ({row.uniqueCode})."
                ),
                # Schéma payload cohérent avec le HIGH fuzzy pour que les
                # clients UI puissent traiter les deux cas avec le même code.
                extra={
                    "duplicates": [
                        {
                            "id": row.id,
                            "firstName": row.firstName,
                            "lastName": row.lastName,
                            "schoolId": dto.schoolId,
                            "score": 1.0,
                            "classification": "EXACT",
                            "matchedFields": ["firstName", "lastName", "birthDate"],
                        }
                    ]
                },
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
            census_duplicate_blocked_total.labels(
                entity="teacher", level="EXACT"
            ).inc()
            raise ConflictError(
                detail=(
                    f"Doublon détecté : {row.firstName} {row.lastName} est déjà "
                    f"enregistré ({row.uniqueCode})."
                ),
                extra={
                    "duplicates": [
                        {
                            "id": row.id,
                            "firstName": row.firstName,
                            "lastName": row.lastName,
                            "schoolId": dto.schoolId,
                            "score": 1.0,
                            "classification": "EXACT",
                            "matchedFields": ["firstName", "lastName", "birthDate"],
                        }
                    ]
                },
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
