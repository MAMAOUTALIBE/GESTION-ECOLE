from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.auth.models import User
from app.modules.schools.models import School
from app.modules.territory.models import Prefecture, Region, SubPrefecture
from app.modules.territory.schemas import (
    CreatePrefectureRequest,
    CreateSubPrefectureRequest,
    PrefectureCounts,
    PrefectureListItem,
    PrefectureRead,
    SubPrefectureCounts,
    SubPrefectureListItem,
    SubPrefectureRead,
)
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


class TerritoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.workflow = WorkflowService(session)

    # ------------------------------------------------------------------
    # REGIONS
    # ------------------------------------------------------------------
    async def list_regions(self, user: User) -> list[Region]:
        """Renvoie les régions accessibles par l'utilisateur (scope-aware).

        - NATIONAL : toutes les régions
        - REGIONAL/PREFECTURE/SUB_PREFECTURE : uniquement la région d'attachement
        - Autres rôles : aucune (frontend devrait utiliser /census/metadata)
        """
        stmt = select(Region).order_by(Region.name.asc())
        if user.role in NATIONAL_SCOPE_ROLES:
            pass
        elif user.regionId:
            stmt = stmt.where(Region.id == user.regionId)
        else:
            stmt = stmt.where(Region.id == "__none__")
        return list((await self.session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    # PREFECTURES
    # ------------------------------------------------------------------
    async def list_prefectures(self, user: User) -> list[PrefectureListItem]:
        stmt = (
            select(Prefecture)
            .options(selectinload(Prefecture.region))
            .order_by(Prefecture.name.asc())
        )
        stmt = self._scope_prefecture_query(stmt, user)
        prefectures = (await self.session.execute(stmt)).scalars().unique().all()

        if not prefectures:
            return []

        # Aggregate _count in 1 query each (cheaper than per-row joins at scale)
        ids = [p.id for p in prefectures]
        sub_counts = dict(
            (
                await self.session.execute(
                    select(SubPrefecture.prefectureId, func.count())
                    .where(SubPrefecture.prefectureId.in_(ids))
                    .group_by(SubPrefecture.prefectureId)
                )
            ).all()
        )
        school_counts = dict(
            (
                await self.session.execute(
                    select(School.prefectureId, func.count())
                    .where(School.prefectureId.in_(ids))
                    .group_by(School.prefectureId)
                )
            ).all()
        )
        user_counts = dict(
            (
                await self.session.execute(
                    select(User.prefectureId, func.count())
                    .where(User.prefectureId.in_(ids))
                    .group_by(User.prefectureId)
                )
            ).all()
        )

        result: list[PrefectureListItem] = []
        for p in prefectures:
            base = PrefectureRead.model_validate(p).model_dump()
            base["_count"] = PrefectureCounts(
                subPrefectures=sub_counts.get(p.id, 0),
                schools=school_counts.get(p.id, 0),
                users=user_counts.get(p.id, 0),
            )
            result.append(PrefectureListItem(**base))
        return result

    async def create_prefecture(
        self, user: User, dto: CreatePrefectureRequest
    ) -> PrefectureRead:
        if user.role not in NATIONAL_SCOPE_ROLES and user.role != UserRole.REGIONAL_ADMIN:
            raise ForbiddenError(
                detail="Seule la région ou le ministère peut ajouter une préfecture"
            )

        region_id = dto.regionId or user.regionId
        if not region_id:
            raise ForbiddenError(detail="Aucune région disponible pour cette création")

        await self._assert_can_manage_region(user, region_id)

        normalized_code = dto.code.strip().upper()
        await self._assert_unique_prefecture_code(normalized_code)

        is_national = user.role in NATIONAL_SCOPE_ROLES
        status = ValidationStatus.APPROVED if is_national else ValidationStatus.SUBMITTED
        now = datetime.now(UTC) if is_national else None

        prefecture = Prefecture(
            name=dto.name.strip(),
            code=normalized_code,
            regionId=region_id,
            status=status,
            createdById=user.id,
            approvedById=user.id if is_national else None,
            approvedAt=now,
        )
        self.session.add(prefecture)
        await self.session.flush()

        if status == ValidationStatus.SUBMITTED:
            await self.workflow.create_validation_request(
                ValidationTarget(
                    entity_type=ValidationEntityType.PREFECTURE,
                    entity_id=prefecture.id,
                    requested_by_id=user.id,
                    reviewer_role=UserRole.MINISTRY_ADMIN,
                    title="Nouvelle préfecture à valider",
                    message=(
                        f"{user.fullName} demande la validation de la préfecture "
                        f"{prefecture.name}."
                    ),
                )
            )

        # Reload with region for the response
        stmt = (
            select(Prefecture)
            .where(Prefecture.id == prefecture.id)
            .options(selectinload(Prefecture.region))
        )
        loaded = (await self.session.execute(stmt)).scalar_one()
        return PrefectureRead.model_validate(loaded)

    # ------------------------------------------------------------------
    # SUB-PREFECTURES
    # ------------------------------------------------------------------
    async def list_sub_prefectures(self, user: User) -> list[SubPrefectureListItem]:
        stmt = (
            select(SubPrefecture)
            .options(
                selectinload(SubPrefecture.prefecture).selectinload(Prefecture.region),
            )
            .order_by(SubPrefecture.name.asc())
        )
        stmt = self._scope_sub_prefecture_query(stmt, user)
        rows = (await self.session.execute(stmt)).scalars().unique().all()

        if not rows:
            return []

        ids = [s.id for s in rows]
        school_counts = dict(
            (
                await self.session.execute(
                    select(School.subPrefectureId, func.count())
                    .where(School.subPrefectureId.in_(ids))
                    .group_by(School.subPrefectureId)
                )
            ).all()
        )
        user_counts = dict(
            (
                await self.session.execute(
                    select(User.subPrefectureId, func.count())
                    .where(User.subPrefectureId.in_(ids))
                    .group_by(User.subPrefectureId)
                )
            ).all()
        )

        result: list[SubPrefectureListItem] = []
        for s in rows:
            base = SubPrefectureRead.model_validate(s).model_dump()
            base["_count"] = SubPrefectureCounts(
                schools=school_counts.get(s.id, 0),
                users=user_counts.get(s.id, 0),
            )
            result.append(SubPrefectureListItem(**base))
        return result

    async def create_sub_prefecture(
        self, user: User, dto: CreateSubPrefectureRequest
    ) -> SubPrefectureRead:
        allowed_roles = (
            *NATIONAL_SCOPE_ROLES,
            UserRole.REGIONAL_ADMIN,
            UserRole.PREFECTURE_ADMIN,
        )
        if user.role not in allowed_roles:
            raise ForbiddenError(
                detail=(
                    "Seule la préfecture, la région ou le ministère "
                    "peut ajouter une sous-préfecture"
                )
            )

        prefecture = await self.session.get(Prefecture, dto.prefectureId)
        if prefecture is None:
            raise NotFoundError(detail="Préfecture introuvable")
        if prefecture.status != ValidationStatus.APPROVED:
            raise ForbiddenError(
                detail=(
                    "La préfecture doit être validée avant de recevoir des "
                    "sous-préfectures"
                )
            )
        await self._assert_can_access_prefecture(user, prefecture.id, prefecture.regionId)

        normalized_code = dto.code.strip().upper()
        await self._assert_unique_sub_prefecture_code(normalized_code)

        needs_review = user.role == UserRole.PREFECTURE_ADMIN
        status = ValidationStatus.SUBMITTED if needs_review else ValidationStatus.APPROVED
        now = None if needs_review else datetime.now(UTC)

        sub = SubPrefecture(
            name=dto.name.strip(),
            code=normalized_code,
            regionId=prefecture.regionId,
            prefectureId=prefecture.id,
            status=status,
            createdById=user.id,
            approvedById=None if needs_review else user.id,
            approvedAt=now,
        )
        self.session.add(sub)
        await self.session.flush()

        if status == ValidationStatus.SUBMITTED:
            await self.workflow.create_validation_request(
                ValidationTarget(
                    entity_type=ValidationEntityType.SUB_PREFECTURE,
                    entity_id=sub.id,
                    requested_by_id=user.id,
                    reviewer_role=UserRole.REGIONAL_ADMIN,
                    reviewer_region_id=prefecture.regionId,
                    title="Nouvelle sous-préfecture à valider",
                    message=(
                        f"{user.fullName} demande la validation de la "
                        f"sous-préfecture {sub.name}."
                    ),
                )
            )

        stmt = (
            select(SubPrefecture)
            .where(SubPrefecture.id == sub.id)
            .options(
                selectinload(SubPrefecture.prefecture).selectinload(Prefecture.region)
            )
        )
        loaded = (await self.session.execute(stmt)).scalar_one()
        return SubPrefectureRead.model_validate(loaded)

    # ------------------------------------------------------------------
    # Scope helpers (mirror NestJS prefectureScopeWhere/subPrefectureScopeWhere)
    # ------------------------------------------------------------------
    def _scope_prefecture_query(self, stmt, user: User):  # type: ignore[no-untyped-def]
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(Prefecture.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(Prefecture.id == user.prefectureId)
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(
                Prefecture.id.in_(
                    select(SubPrefecture.prefectureId).where(
                        SubPrefecture.id == user.subPrefectureId
                    )
                )
            )
        if user.schoolId:
            return stmt.where(
                Prefecture.id.in_(
                    select(School.prefectureId).where(School.id == user.schoolId)
                )
            )
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
        if user.schoolId:
            return stmt.where(
                SubPrefecture.id.in_(
                    select(School.subPrefectureId).where(School.id == user.schoolId)
                )
            )
        return stmt.where(SubPrefecture.id == "__none__")

    async def _assert_can_manage_region(self, user: User, region_id: str) -> None:
        region = await self.session.get(Region, region_id)
        if region is None:
            raise NotFoundError(detail="Région introuvable")
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        if user.role == UserRole.REGIONAL_ADMIN and user.regionId == region_id:
            return
        raise ForbiddenError(detail="Accès non autorisé pour cette région")

    async def _assert_can_access_prefecture(
        self, user: User, prefecture_id: str, region_id: str
    ) -> None:
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        if user.role == UserRole.REGIONAL_ADMIN and user.regionId == region_id:
            return
        if user.role == UserRole.PREFECTURE_ADMIN and user.prefectureId == prefecture_id:
            return
        raise ForbiddenError(detail="Accès non autorisé pour cette préfecture")

    async def _assert_unique_prefecture_code(self, code: str) -> None:
        existing = (
            await self.session.execute(select(Prefecture.id).where(Prefecture.code == code))
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError(detail="Ce code préfecture est déjà utilisé")

    async def _assert_unique_sub_prefecture_code(self, code: str) -> None:
        existing = (
            await self.session.execute(
                select(SubPrefecture.id).where(SubPrefecture.code == code)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError(detail="Ce code sous-préfecture est déjà utilisé")
