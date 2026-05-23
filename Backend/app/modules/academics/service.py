"""Academics service — parents, school years, subjects, assessments, grades,
report cards. Mirrors the NestJS contract exactly so the existing Angular
academics screens work with no changes.
"""
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from app.modules.academics.models import (
    AcademicPeriod,
    Assessment,
    Grade,
    Parent,
    ParentCommunication,
    ReportCard,
    SchoolYear,
    StudentParent,
    Subject,
)
from app.modules.academics.schemas import (
    AcademicPeriodRead,
    AssessmentBriefForGrade,
    AssessmentRead,
    ClassRoomBriefForAssessment,
    CommunicationRead,
    CreateAssessmentRequest,
    CreateParentRequest,
    CreateSchoolYearRequest,
    CreateSubjectRequest,
    GenerateReportCardsRequest,
    GradeRead,
    ParentRead,
    ParentStudentLinkRead,
    ReportCardRead,
    SaveGradesRequest,
    SchoolYearRead,
    StudentBriefForGrade,
    StudentBriefForParent,
    StudentBriefForReport,
    SubjectRead,
    TeacherBrief,
    UpdateParentRequest,
)
from app.modules.auth.models import User
from app.modules.census.models import Student, Teacher
from app.modules.census.schemas import ClassRoomSummary
from app.modules.schools.models import ClassRoom, School
from app.modules.schools.schemas import SchoolEmbedded
from app.modules.workflow.models import AuditLog
from app.shared.enums import (
    AcademicPeriodType,
    AcademicValidationStatus,
    ParentRelationType,
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


_NON_ALPHANUM = re.compile(r"[^A-Z0-9-]", re.IGNORECASE)


class AcademicsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # PARENTS
    # ==================================================================
    async def list_parents(
        self, user: User, *, limit: int = 500,
    ) -> list[ParentRead]:
        scoped_school_ids = self._scoped_school_ids_subq(user)
        stmt = (
            select(Parent)
            .where(
                Parent.id.in_(
                    select(StudentParent.parentId).where(
                        StudentParent.studentId.in_(
                            select(Student.id).where(Student.schoolId.in_(scoped_school_ids))
                        )
                    )
                )
            )
            .options(
                selectinload(Parent.students)
                .selectinload(StudentParent.student)
                .selectinload(Student.school)
                .selectinload(School.region),
                selectinload(Parent.students)
                .selectinload(StudentParent.student)
                .selectinload(Student.classRoom),
                selectinload(Parent.communications),
            )
            .order_by(Parent.updatedAt.desc(), Parent.lastName.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [self._map_parent(p) for p in rows]

    async def create_parent(self, user: User, dto: CreateParentRequest) -> ParentRead:
        await self._resolve_student_links(user, dto.links)
        await self._assert_unique_parent_contact(dto.phone, dto.email)

        parent = Parent(
            firstName=dto.firstName.strip(),
            lastName=dto.lastName.strip(),
            phone=dto.phone.strip(),
            email=_clean(dto.email),
            profession=_clean(dto.profession),
            address=_clean(dto.address),
            preferredLanguage=_clean(dto.preferredLanguage),
        )
        self.session.add(parent)
        await self.session.flush()

        for link in dto.links:
            self.session.add(
                StudentParent(
                    studentId=link.studentId,
                    parentId=parent.id,
                    relation=link.relation,
                    isPrimary=link.isPrimary,
                    isEmergencyContact=(
                        link.isEmergencyContact
                        or link.relation == ParentRelationType.EMERGENCY_CONTACT
                    ),
                )
            )

        self.session.add(
            AuditLog(
                actorId=user.id,
                action="CREATE_PARENT",
                entity="Parent",
                entityId=parent.id,
                metadata_={"studentIds": [link.studentId for link in dto.links]},
            )
        )
        await self.session.flush()
        return await self._reload_parent(parent.id)

    async def update_parent(
        self, user: User, parent_id: str, dto: UpdateParentRequest
    ) -> ParentRead:
        await self._assert_can_access_parent(user, parent_id)
        await self._assert_unique_parent_contact(dto.phone, dto.email, ignored_id=parent_id)

        parent = await self.session.get(Parent, parent_id)
        if parent is None:
            raise NotFoundError(detail="Parent introuvable")

        if dto.firstName is not None:
            parent.firstName = dto.firstName.strip()
        if dto.lastName is not None:
            parent.lastName = dto.lastName.strip()
        if dto.phone is not None:
            parent.phone = dto.phone.strip()
        if dto.email is not None:
            parent.email = _clean(dto.email)
        if dto.profession is not None:
            parent.profession = _clean(dto.profession)
        if dto.address is not None:
            parent.address = _clean(dto.address)
        if dto.preferredLanguage is not None:
            parent.preferredLanguage = _clean(dto.preferredLanguage)

        self.session.add(
            AuditLog(
                actorId=user.id, action="UPDATE_PARENT",
                entity="Parent", entityId=parent.id,
            )
        )
        await self.session.flush()
        return await self._reload_parent(parent.id)

    async def delete_parent(self, user: User, parent_id: str) -> dict[str, bool]:
        await self._assert_can_access_parent(user, parent_id)
        parent = await self.session.get(Parent, parent_id)
        if parent is None:
            raise NotFoundError(detail="Parent introuvable")

        from sqlalchemy import delete  # noqa: PLC0415

        await self.session.execute(
            delete(StudentParent).where(StudentParent.parentId == parent_id)
        )
        await self.session.execute(
            delete(ParentCommunication).where(ParentCommunication.parentId == parent_id)
        )
        await self.session.delete(parent)
        self.session.add(
            AuditLog(
                actorId=user.id, action="DELETE_PARENT",
                entity="Parent", entityId=parent_id,
            )
        )
        await self.session.flush()
        return {"deleted": True}

    # ==================================================================
    # SCHOOL YEARS
    # ==================================================================
    async def list_school_years(self) -> list[SchoolYearRead]:
        stmt = (
            select(SchoolYear)
            .options(selectinload(SchoolYear.periods))
            .order_by(SchoolYear.isActive.desc(), SchoolYear.startDate.desc())
        )
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [
            SchoolYearRead(
                **SchoolYearRead.model_validate(y).model_dump(exclude={"periods"}),
                periods=sorted(
                    [AcademicPeriodRead.model_validate(p) for p in y.periods],
                    key=lambda p: p.order,
                ),
            )
            for y in rows
        ]

    async def create_school_year(
        self, user: User, dto: CreateSchoolYearRequest
    ) -> SchoolYearRead:
        # Permission check is handled by the router @require_roles
        if dto.isActive:
            from sqlalchemy import update  # noqa: PLC0415
            await self.session.execute(update(SchoolYear).values(isActive=False))

        year = SchoolYear(
            name=dto.name.strip(),
            startDate=datetime.combine(dto.startDate, datetime.min.time(), tzinfo=UTC),
            endDate=datetime.combine(dto.endDate, datetime.min.time(), tzinfo=UTC),
            periodType=dto.periodType,
            isActive=dto.isActive,
        )
        self.session.add(year)
        await self.session.flush()

        for spec in self._default_periods(dto.periodType):
            self.session.add(
                AcademicPeriod(
                    name=spec["name"], type=dto.periodType, order=spec["order"],
                    schoolYearId=year.id,
                )
            )

        self.session.add(
            AuditLog(
                actorId=user.id, action="CREATE_SCHOOL_YEAR",
                entity="SchoolYear", entityId=year.id,
            )
        )
        await self.session.flush()

        stmt = (
            select(SchoolYear)
            .where(SchoolYear.id == year.id)
            .options(selectinload(SchoolYear.periods))
        )
        loaded = (await self.session.execute(stmt)).scalar_one()
        return SchoolYearRead(
            **SchoolYearRead.model_validate(loaded).model_dump(exclude={"periods"}),
            periods=sorted(
                [AcademicPeriodRead.model_validate(p) for p in loaded.periods],
                key=lambda p: p.order,
            ),
        )

    # ==================================================================
    # SUBJECTS
    # ==================================================================
    async def list_subjects(self) -> list[SubjectRead]:
        stmt = select(Subject).order_by(Subject.level.asc(), Subject.name.asc())
        rows = (await self.session.execute(stmt)).scalars().all()
        return [SubjectRead.model_validate(s) for s in rows]

    async def create_subject(self, user: User, dto: CreateSubjectRequest) -> SubjectRead:
        code = dto.code.strip().upper()
        existing = (
            await self.session.execute(select(Subject.id).where(Subject.code == code))
        ).scalar_one_or_none()
        if existing:
            raise ConflictError(detail="Ce code matière est déjà utilisé")

        subject = Subject(
            code=code, name=dto.name.strip(),
            level=_clean(dto.level), coefficient=dto.coefficient,
        )
        self.session.add(subject)
        await self.session.flush()

        self.session.add(
            AuditLog(
                actorId=user.id, action="CREATE_SUBJECT",
                entity="Subject", entityId=subject.id,
            )
        )
        await self.session.flush()
        return SubjectRead.model_validate(subject)

    # ==================================================================
    # ASSESSMENTS
    # ==================================================================
    async def list_assessments(
        self, user: User, *, limit: int = 500,
    ) -> list[AssessmentRead]:
        scoped = self._scoped_school_ids_subq(user)
        stmt = (
            select(Assessment)
            .where(
                Assessment.classRoomId.in_(
                    select(ClassRoom.id).where(ClassRoom.schoolId.in_(scoped))
                )
            )
            .options(
                selectinload(Assessment.schoolYear).selectinload(SchoolYear.periods),
                selectinload(Assessment.period),
                selectinload(Assessment.subject),
                selectinload(Assessment.classRoom).selectinload(ClassRoom.school).selectinload(
                    School.region
                ),
                selectinload(Assessment.teacher),
                selectinload(Assessment.grades),
            )
            .order_by(Assessment.createdAt.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [self._map_assessment(a) for a in rows]

    async def create_assessment(
        self, user: User, dto: CreateAssessmentRequest
    ) -> AssessmentRead:
        classroom = await self.session.get(ClassRoom, dto.classRoomId)
        if classroom is None:
            raise NotFoundError(detail="Classe introuvable")
        await self._assert_can_access_school(user, classroom.schoolId)
        await self._assert_period_belongs_to_year(dto.periodId, dto.schoolYearId)

        subject = await self.session.get(Subject, dto.subjectId)
        if subject is None:
            raise NotFoundError(detail="Matière introuvable")

        if dto.teacherId:
            teacher = await self.session.get(Teacher, dto.teacherId)
            if not teacher or teacher.schoolId != classroom.schoolId:
                raise NotFoundError(
                    detail="Enseignant introuvable pour cette école"
                )

        assessment = Assessment(
            title=dto.title.strip(),
            type=dto.type,
            coefficient=dto.coefficient if dto.coefficient is not None else subject.coefficient,
            maxScore=dto.maxScore if dto.maxScore is not None else 20.0,
            assessedAt=dto.assessedAt,
            schoolYearId=dto.schoolYearId,
            periodId=dto.periodId,
            subjectId=dto.subjectId,
            classRoomId=dto.classRoomId,
            teacherId=dto.teacherId,
            actorId=user.id,
        )
        self.session.add(assessment)
        await self.session.flush()

        self.session.add(
            AuditLog(
                actorId=user.id, action="CREATE_ASSESSMENT",
                entity="Assessment", entityId=assessment.id,
            )
        )
        await self.session.flush()
        return await self._reload_assessment(assessment.id)

    async def update_assessment_status(
        self, user: User, assessment_id: str, status: AcademicValidationStatus
    ) -> AssessmentRead:
        stmt = (
            select(Assessment)
            .where(Assessment.id == assessment_id)
            .options(selectinload(Assessment.classRoom))
        )
        assessment = (await self.session.execute(stmt)).scalar_one_or_none()
        if assessment is None:
            raise NotFoundError(detail="Évaluation introuvable")
        await self._assert_can_access_school(user, assessment.classRoom.schoolId)

        assessment.status = status
        await self.session.flush()
        return await self._reload_assessment(assessment.id)

    # ==================================================================
    # GRADES
    # ==================================================================
    async def list_grades(
        self, user: User, assessment_id: str | None = None, *, limit: int = 500,
    ) -> list[GradeRead]:
        scoped = self._scoped_school_ids_subq(user)
        stmt = (
            select(Grade)
            .where(
                Grade.studentId.in_(
                    select(Student.id).where(Student.schoolId.in_(scoped))
                )
            )
            .options(
                selectinload(Grade.assessment).selectinload(Assessment.subject),
                selectinload(Grade.assessment).selectinload(Assessment.period),
                selectinload(Grade.student).selectinload(Student.school).selectinload(
                    School.region
                ),
                selectinload(Grade.student).selectinload(Student.classRoom),
                selectinload(Grade.subject),
                selectinload(Grade.period),
            )
            .order_by(Grade.recordedAt.desc())
        )
        if assessment_id:
            # Une évaluation = ~45 élèves max, on ne tronque pas.
            stmt = stmt.where(Grade.assessmentId == assessment_id)
        else:
            # Garde-fou anti-explosion : 135K+ lignes possibles à l'échelle nationale.
            stmt = stmt.limit(limit)
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [self._map_grade(g) for g in rows]

    async def save_grades(self, user: User, dto: SaveGradesRequest) -> list[GradeRead]:
        stmt = (
            select(Assessment)
            .where(Assessment.id == dto.assessmentId)
            .options(selectinload(Assessment.classRoom))
        )
        assessment = (await self.session.execute(stmt)).scalar_one_or_none()
        if assessment is None:
            raise NotFoundError(detail="Évaluation introuvable")
        await self._assert_can_access_school(user, assessment.classRoom.schoolId)

        student_ids = [g.studentId for g in dto.grades]
        if len(set(student_ids)) != len(student_ids):
            raise ValidationFailedError(
                detail="Un élève apparaît plusieurs fois dans la saisie"
            )

        # Check all students belong to assessment classRoom
        valid = (
            await self.session.execute(
                select(Student.id).where(
                    and_(
                        Student.id.in_(student_ids),
                        Student.schoolId == assessment.classRoom.schoolId,
                        Student.classRoomId == assessment.classRoomId,
                    )
                )
            )
        ).scalars().all()
        if len(valid) != len(student_ids):
            raise ValidationFailedError(
                detail=(
                    "Tous les élèves notés doivent appartenir à la classe de "
                    "l'évaluation"
                )
            )

        over_max = next((g for g in dto.grades if g.score > assessment.maxScore), None)
        if over_max:
            raise ValidationFailedError(
                detail=f"La note ne peut pas dépasser {assessment.maxScore}"
            )

        # Upsert each grade (one row per (assessment, student))
        from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

        rows = [
            {
                "assessmentId": assessment.id,
                "studentId": g.studentId,
                "schoolYearId": assessment.schoolYearId,
                "periodId": assessment.periodId,
                "subjectId": assessment.subjectId,
                "classRoomId": assessment.classRoomId,
                "score": g.score,
                "appreciation": _clean(g.appreciation),
                "status": assessment.status,
                "recordedAt": datetime.now(UTC),
                "updatedAt": datetime.now(UTC),
            }
            for g in dto.grades
        ]
        from app.shared.base import generate_cuid  # noqa: PLC0415

        for r in rows:
            r["id"] = generate_cuid()

        stmt_upsert = pg_insert(Grade).values(rows)
        stmt_upsert = stmt_upsert.on_conflict_do_update(
            constraint="uq_Grade_assessmentId_studentId",
            set_={
                "score": stmt_upsert.excluded.score,
                "appreciation": stmt_upsert.excluded.appreciation,
                "status": stmt_upsert.excluded.status,
                "updatedAt": stmt_upsert.excluded.updatedAt,
            },
        )
        await self.session.execute(stmt_upsert)

        self.session.add(
            AuditLog(
                actorId=user.id, action="SAVE_GRADES",
                entity="Assessment", entityId=assessment.id,
                metadata_={"count": len(dto.grades)},
            )
        )
        await self.session.flush()
        return await self.list_grades(user, dto.assessmentId)

    # ==================================================================
    # REPORT CARDS
    # ==================================================================
    async def list_report_cards(
        self, user: User, *, limit: int = 500,
    ) -> list[ReportCardRead]:
        scoped = self._scoped_school_ids_subq(user)
        stmt = (
            select(ReportCard)
            .where(
                ReportCard.studentId.in_(
                    select(Student.id).where(Student.schoolId.in_(scoped))
                )
            )
            .options(
                selectinload(ReportCard.student).selectinload(Student.school).selectinload(
                    School.region
                ),
                selectinload(ReportCard.student).selectinload(Student.classRoom),
                selectinload(ReportCard.classRoom).selectinload(ClassRoom.school).selectinload(
                    School.region
                ),
                selectinload(ReportCard.schoolYear),
                selectinload(ReportCard.period),
            )
            .order_by(ReportCard.updatedAt.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [self._map_report_card(rc) for rc in rows]

    async def generate_report_cards(
        self, user: User, dto: GenerateReportCardsRequest
    ) -> list[ReportCardRead]:
        await self._assert_period_belongs_to_year(dto.periodId, dto.schoolYearId)

        if dto.classRoomId:
            classroom = await self.session.get(ClassRoom, dto.classRoomId)
            if classroom is None:
                raise NotFoundError(detail="Classe introuvable")
            await self._assert_can_access_school(user, classroom.schoolId)
            classrooms = [classroom]
        else:
            scoped = self._scoped_school_ids_subq(user)
            stmt = select(ClassRoom).where(ClassRoom.schoolId.in_(scoped))
            classrooms = list((await self.session.execute(stmt)).scalars().all())

        generated_ids: list[str] = []
        for classroom in classrooms:
            students = (
                await self.session.execute(
                    select(Student)
                    .where(
                        Student.classRoomId == classroom.id,
                        Student.schoolId == classroom.schoolId,
                    )
                    .order_by(Student.lastName.asc(), Student.firstName.asc())
                )
            ).scalars().all()

            # Compute weighted averages per student
            grades_stmt = (
                select(Grade)
                .where(
                    Grade.classRoomId == classroom.id,
                    Grade.schoolYearId == dto.schoolYearId,
                    Grade.periodId == dto.periodId,
                )
                .options(selectinload(Grade.assessment))
            )
            all_grades = (await self.session.execute(grades_stmt)).scalars().all()
            grades_by_student: dict[str, list[Grade]] = {}
            for g in all_grades:
                grades_by_student.setdefault(g.studentId, []).append(g)

            inputs: list[tuple[Student, float | None]] = []
            for student in students:
                avg = self._weighted_average(grades_by_student.get(student.id, []))
                inputs.append((student, avg))

            ranked = sorted(
                [(s, a) for (s, a) in inputs if a is not None],
                key=lambda t: t[1],
                reverse=True,
            )
            rank_by_student: dict[str, int] = {
                s.id: i + 1 for i, (s, _a) in enumerate(ranked)
            }

            from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415
            from app.shared.base import generate_cuid  # noqa: PLC0415

            for student, avg in inputs:
                code = self._report_card_code(student.uniqueCode, dto.periodId)
                stmt_upsert = (
                    pg_insert(ReportCard)
                    .values(
                        id=generate_cuid(),
                        studentId=student.id,
                        classRoomId=classroom.id,
                        schoolYearId=dto.schoolYearId,
                        periodId=dto.periodId,
                        average=avg,
                        rank=rank_by_student.get(student.id),
                        totalStudents=len(students),
                        verificationCode=code,
                    )
                    .on_conflict_do_update(
                        constraint="uq_ReportCard_studentId_periodId",
                        set_={
                            "classRoomId": classroom.id,
                            "average": avg,
                            "rank": rank_by_student.get(student.id),
                            "totalStudents": len(students),
                        },
                    )
                    .returning(ReportCard.id)
                )
                rid = (await self.session.execute(stmt_upsert)).scalar_one()
                generated_ids.append(rid)

        self.session.add(
            AuditLog(
                actorId=user.id, action="GENERATE_REPORT_CARDS",
                entity="ReportCard",
                metadata_={
                    "count": len(generated_ids),
                    "schoolYearId": dto.schoolYearId,
                    "periodId": dto.periodId,
                },
            )
        )
        await self.session.flush()

        if not generated_ids:
            return []

        stmt = (
            select(ReportCard)
            .where(ReportCard.id.in_(generated_ids))
            .options(
                selectinload(ReportCard.student).selectinload(Student.school).selectinload(
                    School.region
                ),
                selectinload(ReportCard.student).selectinload(Student.classRoom),
                selectinload(ReportCard.classRoom).selectinload(ClassRoom.school).selectinload(
                    School.region
                ),
                selectinload(ReportCard.schoolYear),
                selectinload(ReportCard.period),
            )
            .order_by(ReportCard.classRoomId.asc(), ReportCard.rank.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        return [self._map_report_card(rc) for rc in rows]

    async def update_report_card_status(
        self, user: User, report_card_id: str, status: AcademicValidationStatus
    ) -> ReportCardRead:
        stmt = (
            select(ReportCard)
            .where(ReportCard.id == report_card_id)
            .options(selectinload(ReportCard.student))
        )
        card = (await self.session.execute(stmt)).scalar_one_or_none()
        if card is None:
            raise NotFoundError(detail="Bulletin introuvable")
        await self._assert_can_access_school(user, card.student.schoolId)

        card.status = status
        if status == AcademicValidationStatus.VALIDATED:
            card.issuedAt = datetime.now(UTC)
        await self.session.flush()
        return await self._reload_report_card(card.id)

    # ==================================================================
    # HELPERS
    # ==================================================================
    def _scoped_school_ids_subq(self, user: User):  # type: ignore[no-untyped-def]
        if user.role in NATIONAL_SCOPE_ROLES:
            return select(School.id)
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return select(School.id).where(School.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return select(School.id).where(School.prefectureId == user.prefectureId)
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return select(School.id).where(School.subPrefectureId == user.subPrefectureId)
        if user.schoolId:
            return select(School.id).where(School.id == user.schoolId)
        return select(School.id).where(School.id == "__none__")

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

    async def _assert_can_access_parent(self, user: User, parent_id: str) -> None:
        scoped = self._scoped_school_ids_subq(user)
        exists = (
            await self.session.execute(
                select(Parent.id).where(
                    and_(
                        Parent.id == parent_id,
                        Parent.id.in_(
                            select(StudentParent.parentId).where(
                                StudentParent.studentId.in_(
                                    select(Student.id).where(Student.schoolId.in_(scoped))
                                )
                            )
                        ),
                    )
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            raise NotFoundError(detail="Parent introuvable")

    async def _resolve_student_links(
        self, user: User, links: list[Any]
    ) -> None:
        keys = [f"{link.studentId}:{link.relation.value}" for link in links]
        if len(set(keys)) != len(keys):
            raise ValidationFailedError(
                detail="La même relation parent-élève ne peut pas être répétée"
            )

        scoped = self._scoped_school_ids_subq(user)
        student_ids = list({link.studentId for link in links})
        valid = (
            await self.session.execute(
                select(Student.id).where(
                    Student.id.in_(student_ids),
                    Student.schoolId.in_(scoped),
                )
            )
        ).scalars().all()
        if len(valid) != len(student_ids):
            raise ValidationFailedError(
                detail="Tous les élèves liés doivent appartenir au périmètre utilisateur"
            )

    async def _assert_unique_parent_contact(
        self,
        phone: str | None = None,
        email: str | None = None,
        ignored_id: str | None = None,
    ) -> None:
        clean_phone = _clean(phone)
        clean_email = _clean(email)

        if clean_phone:
            stmt = select(Parent.id).where(Parent.phone == clean_phone)
            if ignored_id:
                stmt = stmt.where(Parent.id != ignored_id)
            existing = (await self.session.execute(stmt)).scalar_one_or_none()
            if existing:
                raise ConflictError(
                    detail="Ce numéro de téléphone parent est déjà utilisé"
                )

        if clean_email:
            stmt = select(Parent.id).where(Parent.email == clean_email)
            if ignored_id:
                stmt = stmt.where(Parent.id != ignored_id)
            existing = (await self.session.execute(stmt)).scalar_one_or_none()
            if existing:
                raise ConflictError(detail="Cet email parent est déjà utilisé")

    async def _assert_period_belongs_to_year(
        self, period_id: str, school_year_id: str
    ) -> None:
        existing = (
            await self.session.execute(
                select(AcademicPeriod.id).where(
                    and_(
                        AcademicPeriod.id == period_id,
                        AcademicPeriod.schoolYearId == school_year_id,
                    )
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            raise ValidationFailedError(
                detail="La période ne correspond pas à cette année scolaire"
            )

    @staticmethod
    def _default_periods(period_type: AcademicPeriodType) -> list[dict[str, Any]]:
        if period_type == AcademicPeriodType.SEMESTER:
            return [
                {"name": "Semestre 1", "order": 1},
                {"name": "Semestre 2", "order": 2},
            ]
        return [
            {"name": "Trimestre 1", "order": 1},
            {"name": "Trimestre 2", "order": 2},
            {"name": "Trimestre 3", "order": 3},
        ]

    @staticmethod
    def _weighted_average(grades: list[Grade]) -> float | None:
        if not grades:
            return None
        weighted = 0.0
        coef_sum = 0.0
        for g in grades:
            try:
                max_score = g.assessment.maxScore or 20.0
                coef = g.assessment.coefficient or 1.0
            except Exception:
                continue
            weighted += (g.score / max_score) * 20 * coef
            coef_sum += coef
        if coef_sum == 0:
            return None
        return round((weighted / coef_sum) * 100) / 100

    @staticmethod
    def _report_card_code(unique_code: str, period_id: str) -> str:
        raw = f"BUL-{unique_code}-{period_id[:8]}"
        return _NON_ALPHANUM.sub("", raw).upper()

    # --- Reload helpers (return mapped responses with relationships) ---
    async def _reload_parent(self, parent_id: str) -> ParentRead:
        stmt = (
            select(Parent)
            .where(Parent.id == parent_id)
            .options(
                selectinload(Parent.students)
                .selectinload(StudentParent.student)
                .selectinload(Student.school)
                .selectinload(School.region),
                selectinload(Parent.students)
                .selectinload(StudentParent.student)
                .selectinload(Student.classRoom),
                selectinload(Parent.communications),
            )
        )
        parent = (await self.session.execute(stmt)).scalar_one()
        return self._map_parent(parent)

    async def _reload_assessment(self, assessment_id: str) -> AssessmentRead:
        stmt = (
            select(Assessment)
            .where(Assessment.id == assessment_id)
            .options(
                selectinload(Assessment.schoolYear),
                selectinload(Assessment.period),
                selectinload(Assessment.subject),
                selectinload(Assessment.classRoom).selectinload(ClassRoom.school).selectinload(
                    School.region
                ),
                selectinload(Assessment.teacher),
                selectinload(Assessment.grades),
            )
        )
        a = (await self.session.execute(stmt)).scalar_one()
        return self._map_assessment(a)

    async def _reload_report_card(self, rc_id: str) -> ReportCardRead:
        stmt = (
            select(ReportCard)
            .where(ReportCard.id == rc_id)
            .options(
                selectinload(ReportCard.student).selectinload(Student.school).selectinload(
                    School.region
                ),
                selectinload(ReportCard.student).selectinload(Student.classRoom),
                selectinload(ReportCard.classRoom).selectinload(ClassRoom.school).selectinload(
                    School.region
                ),
                selectinload(ReportCard.schoolYear),
                selectinload(ReportCard.period),
            )
        )
        rc = (await self.session.execute(stmt)).scalar_one()
        return self._map_report_card(rc)

    # ----- Mapping helpers -----
    @staticmethod
    def _map_parent(parent: Parent) -> ParentRead:
        students_payload: list[ParentStudentLinkRead] = []
        try:
            for link in list(parent.students):
                student = link.student
                students_payload.append(
                    ParentStudentLinkRead(
                        id=link.id,
                        relation=link.relation,
                        isPrimary=link.isPrimary,
                        isEmergencyContact=link.isEmergencyContact,
                        student=StudentBriefForParent(
                            id=student.id,
                            firstName=student.firstName,
                            lastName=student.lastName,
                            fullName=f"{student.firstName} {student.lastName}",
                            gender=student.gender,
                            uniqueCode=student.uniqueCode,
                            school=SchoolEmbedded.model_validate(student.school)
                            if student.school else None,
                            classRoom=ClassRoomSummary(
                                id=student.classRoom.id,
                                name=student.classRoom.name,
                                level=student.classRoom.level,
                                maxStudents=student.classRoom.maxStudents,
                                schoolYear=student.classRoom.schoolYear,
                                schoolId=student.classRoom.schoolId,
                                school=None,
                                createdAt=student.classRoom.createdAt,
                                updatedAt=student.classRoom.updatedAt,
                            ) if student.classRoom else None,
                        ),
                    )
                )
        except Exception:
            pass

        comms: list[CommunicationRead] = []
        try:
            comms = [
                CommunicationRead.model_validate(c)
                for c in sorted(parent.communications, key=lambda c: c.createdAt, reverse=True)[:3]
            ]
        except Exception:
            pass

        return ParentRead(
            id=parent.id,
            firstName=parent.firstName,
            lastName=parent.lastName,
            fullName=f"{parent.firstName} {parent.lastName}",
            phone=parent.phone,
            email=parent.email,
            profession=parent.profession,
            address=parent.address,
            preferredLanguage=parent.preferredLanguage,
            otpVerifiedAt=parent.otpVerifiedAt,
            createdAt=parent.createdAt,
            updatedAt=parent.updatedAt,
            students=students_payload,
            communications=comms,
        )

    @staticmethod
    def _map_assessment(a: Assessment) -> AssessmentRead:
        try:
            grades_count = len(list(a.grades))
        except Exception:
            grades_count = 0

        classroom_payload: ClassRoomBriefForAssessment | None = None
        if a.classRoom:
            classroom_payload = ClassRoomBriefForAssessment(
                id=a.classRoom.id,
                name=a.classRoom.name,
                level=a.classRoom.level,
                schoolId=a.classRoom.schoolId,
                school=SchoolEmbedded.model_validate(a.classRoom.school)
                if a.classRoom.school else None,
            )

        return AssessmentRead(
            id=a.id,
            title=a.title,
            type=a.type,
            coefficient=a.coefficient,
            maxScore=a.maxScore,
            assessedAt=a.assessedAt,
            status=a.status,
            schoolYearId=a.schoolYearId,
            periodId=a.periodId,
            subjectId=a.subjectId,
            classRoomId=a.classRoomId,
            teacherId=a.teacherId,
            schoolYear=SchoolYearRead.model_validate(a.schoolYear).model_copy(
                update={"periods": []}
            ) if a.schoolYear else None,
            period=AcademicPeriodRead.model_validate(a.period) if a.period else None,
            subject=SubjectRead.model_validate(a.subject) if a.subject else None,
            classRoom=classroom_payload,
            teacher=TeacherBrief.model_validate(a.teacher) if a.teacher else None,
            gradesCount=grades_count,
            createdAt=a.createdAt,
            updatedAt=a.updatedAt,
        )

    @staticmethod
    def _map_grade(g: Grade) -> GradeRead:
        student_payload = StudentBriefForGrade(
            id=g.student.id,
            firstName=g.student.firstName,
            lastName=g.student.lastName,
            fullName=f"{g.student.firstName} {g.student.lastName}",
            uniqueCode=g.student.uniqueCode,
            school=SchoolEmbedded.model_validate(g.student.school)
            if g.student.school else None,
            classRoom=ClassRoomSummary(
                id=g.student.classRoom.id,
                name=g.student.classRoom.name,
                level=g.student.classRoom.level,
                maxStudents=g.student.classRoom.maxStudents,
                schoolYear=g.student.classRoom.schoolYear,
                schoolId=g.student.classRoom.schoolId,
                school=None,
                createdAt=g.student.classRoom.createdAt,
                updatedAt=g.student.classRoom.updatedAt,
            ) if g.student.classRoom else None,
        )

        assessment_brief: AssessmentBriefForGrade | None = None
        if g.assessment:
            assessment_brief = AssessmentBriefForGrade(
                id=g.assessment.id,
                title=g.assessment.title,
                type=g.assessment.type,
                maxScore=g.assessment.maxScore,
                coefficient=g.assessment.coefficient,
                subject=SubjectRead.model_validate(g.assessment.subject)
                if g.assessment.subject else None,
                period=AcademicPeriodRead.model_validate(g.assessment.period)
                if g.assessment.period else None,
                classRoomId=g.assessment.classRoomId,
            )

        return GradeRead(
            id=g.id,
            assessmentId=g.assessmentId,
            studentId=g.studentId,
            score=g.score,
            appreciation=g.appreciation,
            status=g.status,
            recordedAt=g.recordedAt,
            updatedAt=g.updatedAt,
            student=student_payload,
            assessment=assessment_brief,
            subject=SubjectRead.model_validate(g.subject) if g.subject else None,
            period=AcademicPeriodRead.model_validate(g.period) if g.period else None,
        )

    @staticmethod
    def _map_report_card(rc: ReportCard) -> ReportCardRead:
        student_payload: StudentBriefForReport | None = None
        if rc.student:
            student_payload = StudentBriefForReport(
                id=rc.student.id,
                firstName=rc.student.firstName,
                lastName=rc.student.lastName,
                fullName=f"{rc.student.firstName} {rc.student.lastName}",
                uniqueCode=rc.student.uniqueCode,
                school=SchoolEmbedded.model_validate(rc.student.school)
                if rc.student.school else None,
                classRoom=ClassRoomSummary(
                    id=rc.student.classRoom.id,
                    name=rc.student.classRoom.name,
                    level=rc.student.classRoom.level,
                    maxStudents=rc.student.classRoom.maxStudents,
                    schoolYear=rc.student.classRoom.schoolYear,
                    schoolId=rc.student.classRoom.schoolId,
                    school=None,
                    createdAt=rc.student.classRoom.createdAt,
                    updatedAt=rc.student.classRoom.updatedAt,
                ) if rc.student.classRoom else None,
            )

        classroom_payload: ClassRoomBriefForAssessment | None = None
        if rc.classRoom:
            classroom_payload = ClassRoomBriefForAssessment(
                id=rc.classRoom.id,
                name=rc.classRoom.name,
                level=rc.classRoom.level,
                schoolId=rc.classRoom.schoolId,
                school=SchoolEmbedded.model_validate(rc.classRoom.school)
                if rc.classRoom.school else None,
            )

        return ReportCardRead(
            id=rc.id,
            studentId=rc.studentId,
            classRoomId=rc.classRoomId,
            schoolYearId=rc.schoolYearId,
            periodId=rc.periodId,
            average=rc.average,
            rank=rc.rank,
            totalStudents=rc.totalStudents,
            teacherComment=rc.teacherComment,
            directorComment=rc.directorComment,
            verificationCode=rc.verificationCode,
            status=rc.status,
            issuedAt=rc.issuedAt,
            createdAt=rc.createdAt,
            updatedAt=rc.updatedAt,
            student=student_payload,
            classRoom=classroom_payload,
            # Construit SchoolYearRead à la main pour éviter de toucher à la
            # relation `periods` qui est lazy='raise' (la liste serait vide de
            # toute façon, on n'a pas besoin de la précharger pour un bulletin).
            schoolYear=SchoolYearRead(
                id=rc.schoolYear.id,
                name=rc.schoolYear.name,
                startDate=rc.schoolYear.startDate,
                endDate=rc.schoolYear.endDate,
                periodType=rc.schoolYear.periodType,
                isActive=rc.schoolYear.isActive,
                createdAt=rc.schoolYear.createdAt,
                updatedAt=rc.schoolYear.updatedAt,
                periods=[],
            ) if rc.schoolYear else None,
            period=AcademicPeriodRead.model_validate(rc.period) if rc.period else None,
        )
