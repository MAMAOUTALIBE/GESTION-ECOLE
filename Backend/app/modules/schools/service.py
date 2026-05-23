from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationFailedError,
)
from app.modules.attendance.models import AttendanceRecord
from app.modules.auth.models import User
from app.modules.census.models import Student, Teacher
from app.modules.schools.models import ClassRoom, School
from app.modules.schools.schemas import (
    ClassRoomBrief,
    ClassRoomRead,
    CreateClassRoomRequest,
    CreateSchoolRequest,
    SchoolBriefForClass,
    SchoolCounts,
    SchoolRead,
    UpdateClassRoomRequest,
    UpdateSchoolRequest,
)
from app.modules.territory.models import Prefecture, SubPrefecture
from app.modules.workflow.service import ValidationTarget, WorkflowService
from app.shared.enums import (
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


class SchoolsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.workflow = WorkflowService(session)

    # ------------------------------------------------------------------
    # SCHOOLS
    # ------------------------------------------------------------------
    async def list_schools(self, user: User) -> list[SchoolRead]:
        stmt = (
            select(School)
            .options(
                selectinload(School.region),
                selectinload(School.prefectureRef),
                selectinload(School.subPrefecture),
                selectinload(School.classes),
            )
            .order_by(School.name.asc())
        )
        stmt = self._scope_school_query(stmt, user)
        schools = (await self.session.execute(stmt)).scalars().unique().all()
        if not schools:
            return []

        counts = await self._school_counts([s.id for s in schools])
        return [self._map_school(s, counts.get(s.id, SchoolCounts())) for s in schools]

    async def get_school(self, user: User, school_id: str) -> SchoolRead:
        await self._assert_can_access_school(user, school_id)

        stmt = (
            select(School)
            .where(School.id == school_id)
            .options(
                selectinload(School.region),
                selectinload(School.prefectureRef),
                selectinload(School.subPrefecture),
                selectinload(School.classes),
            )
        )
        school = (await self.session.execute(stmt)).scalar_one_or_none()
        if school is None:
            raise NotFoundError(detail="École introuvable")

        counts = await self._school_counts([school.id])
        return self._map_school(school, counts.get(school.id, SchoolCounts()))

    async def create_school(self, user: User, dto: CreateSchoolRequest) -> SchoolRead:
        territory = await self._resolve_school_territory(user, dto)
        normalized_code = dto.code.strip().upper()
        await self._assert_unique_school_code(normalized_code)

        is_sub_prefecture_admin = user.role == UserRole.SUB_PREFECTURE_ADMIN
        status = (
            ValidationStatus.SUBMITTED if is_sub_prefecture_admin else ValidationStatus.APPROVED
        )
        now = None if is_sub_prefecture_admin else datetime.now(UTC)

        school = School(
            name=dto.name.strip(),
            code=normalized_code,
            regionId=territory["regionId"],
            prefectureId=territory["prefectureId"],
            subPrefectureId=territory["subPrefectureId"],
            prefecture=_clean(dto.prefecture) or territory["prefectureName"],
            commune=_clean(dto.commune) or territory["subPrefectureName"],
            type=_clean(dto.type),
            address=_clean(dto.address),
            phone=_clean(dto.phone),
            latitude=dto.latitude,
            longitude=dto.longitude,
            status=status,
            createdById=user.id,
            approvedById=None if is_sub_prefecture_admin else user.id,
            approvedAt=now,
        )
        self.session.add(school)
        await self.session.flush()

        if status == ValidationStatus.SUBMITTED and territory["prefectureId"]:
            await self.workflow.create_validation_request(
                ValidationTarget(
                    entity_type=ValidationEntityType.SCHOOL,
                    entity_id=school.id,
                    requested_by_id=user.id,
                    reviewer_role=UserRole.PREFECTURE_ADMIN,
                    reviewer_prefecture_id=territory["prefectureId"],
                    title="Nouvelle école à valider",
                    message=f"{user.fullName} demande la validation de l'école {school.name}.",
                )
            )

        return await self.get_school(user, school.id)

    async def update_school(
        self, user: User, school_id: str, dto: UpdateSchoolRequest
    ) -> SchoolRead:
        await self._assert_can_access_school(user, school_id)
        if dto.regionId:
            await self._assert_can_manage_region(user, dto.regionId)

        school = await self.session.get(School, school_id)
        if school is None:
            raise NotFoundError(detail="École introuvable")

        normalized_code = dto.code.strip().upper() if dto.code else None
        if normalized_code:
            await self._assert_unique_school_code(normalized_code, ignored_id=school_id)
            school.code = normalized_code

        if dto.name is not None:
            school.name = dto.name.strip()
        if dto.regionId is not None:
            school.regionId = dto.regionId
        if dto.prefectureId is not None:
            school.prefectureId = dto.prefectureId
        if dto.subPrefectureId is not None:
            school.subPrefectureId = dto.subPrefectureId
        if dto.prefecture is not None:
            school.prefecture = _clean(dto.prefecture)
        if dto.commune is not None:
            school.commune = _clean(dto.commune)
        if dto.type is not None:
            school.type = _clean(dto.type)
        if dto.address is not None:
            school.address = _clean(dto.address)
        if dto.phone is not None:
            school.phone = _clean(dto.phone)
        if dto.latitude is not None:
            school.latitude = dto.latitude
        if dto.longitude is not None:
            school.longitude = dto.longitude

        await self.session.flush()
        return await self.get_school(user, school.id)

    async def delete_school(self, user: User, school_id: str) -> dict[str, bool]:
        await self._assert_can_access_school(user, school_id)
        school = await self.session.get(School, school_id)
        if school is None:
            raise NotFoundError(detail="École introuvable")

        # Check for any blocking relations
        classes_count = await self._count(ClassRoom, ClassRoom.schoolId == school_id)
        students_count = await self._count(Student, Student.schoolId == school_id)
        teachers_count = await self._count(Teacher, Teacher.schoolId == school_id)
        users_count = await self._count(User, User.schoolId == school_id)
        attendances_count = await self._count(
            AttendanceRecord, AttendanceRecord.schoolId == school_id
        )

        if (
            classes_count + students_count + teachers_count + users_count + attendances_count
            > 0
        ):
            raise ValidationFailedError(detail="Impossible de supprimer une école déjà utilisée")

        await self.session.delete(school)
        await self.session.flush()
        return {"deleted": True}

    # ------------------------------------------------------------------
    # CLASSES
    # ------------------------------------------------------------------
    async def list_classes(self, user: User) -> list[ClassRoomRead]:
        # Subquery: school IDs visible to user
        school_subq = self._scope_school_query(select(School.id), user).subquery()

        stmt = (
            select(ClassRoom)
            .where(ClassRoom.schoolId.in_(select(school_subq)))
            .options(selectinload(ClassRoom.school).selectinload(School.region))
            .order_by(ClassRoom.level.asc(), ClassRoom.name.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().unique().all()
        if not rows:
            return []
        counts = await self._class_counts([c.id for c in rows])
        return [self._map_class(c, *counts.get(c.id, (0, 0))) for c in rows]

    async def get_class(self, user: User, class_id: str) -> ClassRoomRead:
        stmt = (
            select(ClassRoom)
            .where(ClassRoom.id == class_id)
            .options(selectinload(ClassRoom.school).selectinload(School.region))
        )
        classroom = (await self.session.execute(stmt)).scalar_one_or_none()
        if classroom is None:
            raise NotFoundError(detail="Classe introuvable")

        await self._assert_can_access_school(user, classroom.schoolId)
        counts = await self._class_counts([classroom.id])
        return self._map_class(classroom, *counts.get(classroom.id, (0, 0)))

    async def create_class(self, user: User, dto: CreateClassRoomRequest) -> ClassRoomRead:
        await self._assert_can_access_school(user, dto.schoolId)
        name = dto.name.strip()
        await self._assert_unique_class_name(dto.schoolId, name)

        classroom = ClassRoom(
            name=name,
            level=_clean(dto.level),
            maxStudents=dto.maxStudents,
            schoolYear=_clean(dto.schoolYear),
            schoolId=dto.schoolId,
        )
        self.session.add(classroom)
        await self.session.flush()
        return await self.get_class(user, classroom.id)

    async def update_class(
        self, user: User, class_id: str, dto: UpdateClassRoomRequest
    ) -> ClassRoomRead:
        classroom = await self.session.get(ClassRoom, class_id)
        if classroom is None:
            raise NotFoundError(detail="Classe introuvable")
        await self._assert_can_access_school(user, classroom.schoolId)
        if dto.schoolId:
            await self._assert_can_access_school(user, dto.schoolId)

        target_school_id = dto.schoolId or classroom.schoolId
        target_name = dto.name.strip() if dto.name else classroom.name
        if dto.schoolId or dto.name:
            await self._assert_unique_class_name(target_school_id, target_name, ignored_id=class_id)

        if dto.name is not None:
            classroom.name = dto.name.strip()
        if dto.level is not None:
            classroom.level = _clean(dto.level)
        if dto.maxStudents is not None:
            classroom.maxStudents = dto.maxStudents
        if dto.schoolYear is not None:
            classroom.schoolYear = _clean(dto.schoolYear)
        if dto.schoolId is not None:
            classroom.schoolId = dto.schoolId

        await self.session.flush()
        return await self.get_class(user, classroom.id)

    async def delete_class(self, user: User, class_id: str) -> dict[str, bool]:
        classroom = await self.session.get(ClassRoom, class_id)
        if classroom is None:
            raise NotFoundError(detail="Classe introuvable")
        await self._assert_can_access_school(user, classroom.schoolId)

        students_count = await self._count(Student, Student.classRoomId == class_id)
        if students_count > 0:
            raise ValidationFailedError(
                detail="Impossible de supprimer une classe déjà utilisée"
            )
        # Teachers M2M check via _ClassRoomTeacher
        from sqlalchemy import literal_column, text  # noqa: PLC0415

        teachers_count_row = await self.session.execute(
            text('SELECT COUNT(*) FROM "_ClassRoomTeacher" WHERE "A" = :cid'),
            {"cid": class_id},
        )
        if (teachers_count_row.scalar_one() or 0) > 0:
            raise ValidationFailedError(
                detail="Impossible de supprimer une classe affectée à des enseignants"
            )
        _ = literal_column  # silence unused import in some linters

        await self.session.delete(classroom)
        await self.session.flush()
        return {"deleted": True}

    # ==================================================================
    # Helpers — scope, access, uniqueness, mapping
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

    async def _assert_can_manage_region(self, user: User, region_id: str) -> None:
        from app.modules.territory.models import Region  # noqa: PLC0415

        region = await self.session.get(Region, region_id)
        if region is None:
            raise NotFoundError(detail="Région introuvable")
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        if user.role == UserRole.REGIONAL_ADMIN and user.regionId == region_id:
            return
        raise ForbiddenError(detail="Accès non autorisé pour cette région")

    async def _resolve_school_territory(
        self, user: User, dto: CreateSchoolRequest
    ) -> dict[str, Any]:
        sub_prefecture_id = dto.subPrefectureId or user.subPrefectureId
        if sub_prefecture_id:
            stmt = (
                select(SubPrefecture)
                .where(SubPrefecture.id == sub_prefecture_id)
                .options(selectinload(SubPrefecture.prefecture))
            )
            sub = (await self.session.execute(stmt)).scalar_one_or_none()
            if sub is None:
                raise NotFoundError(detail="Sous-préfecture introuvable")
            if sub.status != ValidationStatus.APPROVED:
                raise ForbiddenError(
                    detail="La sous-préfecture doit être validée avant de recevoir des écoles"
                )
            allowed = (
                user.role in NATIONAL_SCOPE_ROLES
                or (user.role in REGIONAL_SCOPE_ROLES and user.regionId == sub.regionId)
                or (
                    user.role in PREFECTURE_SCOPE_ROLES
                    and user.prefectureId == sub.prefectureId
                )
                or (
                    user.role == UserRole.SUB_PREFECTURE_ADMIN
                    and user.subPrefectureId == sub.id
                )
            )
            if not allowed:
                raise ForbiddenError(detail="Accès non autorisé pour cette sous-préfecture")
            return {
                "regionId": sub.regionId,
                "prefectureId": sub.prefectureId,
                "subPrefectureId": sub.id,
                "prefectureName": sub.prefecture.name if sub.prefecture else None,
                "subPrefectureName": sub.name,
            }

        region_id = dto.regionId or user.regionId
        if not region_id:
            raise ForbiddenError(detail="Aucune région disponible pour cette école")
        await self._assert_can_manage_region(user, region_id)
        return {
            "regionId": region_id,
            "prefectureId": dto.prefectureId,
            "subPrefectureId": None,
            "prefectureName": _clean(dto.prefecture),
            "subPrefectureName": _clean(dto.commune),
        }

    async def _assert_unique_school_code(
        self, code: str, ignored_id: str | None = None
    ) -> None:
        stmt = select(School.id).where(School.code == code)
        if ignored_id:
            stmt = stmt.where(School.id != ignored_id)
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            raise ConflictError(detail="Ce code école est déjà utilisé")

    async def _assert_unique_class_name(
        self, school_id: str, name: str, ignored_id: str | None = None
    ) -> None:
        stmt = select(ClassRoom.id).where(
            and_(ClassRoom.schoolId == school_id, ClassRoom.name == name)
        )
        if ignored_id:
            stmt = stmt.where(ClassRoom.id != ignored_id)
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            raise ConflictError(
                detail="Une classe porte déjà ce nom dans cette école"
            )

    async def _count(self, model: Any, condition: Any) -> int:
        return (
            await self.session.execute(select(func.count()).select_from(model).where(condition))
        ).scalar_one()

    async def _school_counts(self, school_ids: list[str]) -> dict[str, SchoolCounts]:
        if not school_ids:
            return {}

        classes = dict(
            (
                await self.session.execute(
                    select(ClassRoom.schoolId, func.count())
                    .where(ClassRoom.schoolId.in_(school_ids))
                    .group_by(ClassRoom.schoolId)
                )
            ).all()
        )
        students = dict(
            (
                await self.session.execute(
                    select(Student.schoolId, func.count())
                    .where(Student.schoolId.in_(school_ids))
                    .group_by(Student.schoolId)
                )
            ).all()
        )
        teachers = dict(
            (
                await self.session.execute(
                    select(Teacher.schoolId, func.count())
                    .where(Teacher.schoolId.in_(school_ids))
                    .group_by(Teacher.schoolId)
                )
            ).all()
        )
        return {
            sid: SchoolCounts(
                classes=classes.get(sid, 0),
                students=students.get(sid, 0),
                teachers=teachers.get(sid, 0),
            )
            for sid in school_ids
        }

    async def _class_counts(self, class_ids: list[str]) -> dict[str, tuple[int, int]]:
        if not class_ids:
            return {}
        students = dict(
            (
                await self.session.execute(
                    select(Student.classRoomId, func.count())
                    .where(Student.classRoomId.in_(class_ids))
                    .group_by(Student.classRoomId)
                )
            ).all()
        )
        from sqlalchemy import text  # noqa: PLC0415

        teachers_rows = (
            await self.session.execute(
                text(
                    'SELECT "A" AS class_id, COUNT(*) AS c FROM "_ClassRoomTeacher" '
                    'WHERE "A" = ANY(:ids) GROUP BY "A"'
                ),
                {"ids": class_ids},
            )
        ).all()
        teachers = {row[0]: row[1] for row in teachers_rows}

        return {cid: (students.get(cid, 0), teachers.get(cid, 0)) for cid in class_ids}

    @staticmethod
    def _map_school(school: School, counts: SchoolCounts) -> SchoolRead:
        return SchoolRead(
            id=school.id,
            name=school.name,
            code=school.code,
            regionId=school.regionId,
            region=school.region,  # type: ignore[arg-type]
            prefectureId=school.prefectureId,
            subPrefectureId=school.subPrefectureId,
            prefectureRef=school.prefectureRef,  # type: ignore[arg-type]
            subPrefecture=school.subPrefecture,  # type: ignore[arg-type]
            prefecture=school.prefecture,
            commune=school.commune,
            status=school.status,
            rejectionReason=school.rejectionReason,
            type=school.type,
            address=school.address,
            phone=school.phone,
            latitude=school.latitude,
            longitude=school.longitude,
            classes=[ClassRoomBrief.model_validate(c) for c in school.classes],
            counts=counts,
            # Phase 10 — Infrastructure structurée
            waterSource=school.waterSource,
            electricitySource=school.electricitySource,
            internetAvailable=school.internetAvailable,
            toiletsBoys=school.toiletsBoys,
            toiletsGirls=school.toiletsGirls,
            toiletsAccessible=school.toiletsAccessible,
            classroomsTotal=school.classroomsTotal,
            classroomsUsable=school.classroomsUsable,
            buildingCondition=school.buildingCondition,
            buildingYear=school.buildingYear,
            multiShift=school.multiShift,
            distanceToHealthCenterKm=school.distanceToHealthCenterKm,
            affiliation=school.affiliation,
            createdAt=school.createdAt,
            updatedAt=school.updatedAt,
        )

    @staticmethod
    def _map_class(
        classroom: ClassRoom, students_count: int, teachers_count: int
    ) -> ClassRoomRead:
        return ClassRoomRead(
            id=classroom.id,
            name=classroom.name,
            level=classroom.level,
            maxStudents=classroom.maxStudents,
            schoolYear=classroom.schoolYear,
            schoolId=classroom.schoolId,
            school=SchoolBriefForClass.model_validate(classroom.school)
            if classroom.school
            else None,
            studentsCount=students_count,
            teachersCount=teachers_count,
            createdAt=classroom.createdAt,
            updatedAt=classroom.updatedAt,
        )


def _clean(value: str | None) -> str | None:
    """Trim, return None on empty (matches NestJS clean())."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
