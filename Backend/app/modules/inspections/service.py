"""Inspections service — planification, déroulé, score, plan d'action."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.auth.models import User
from app.modules.census.service import CensusService
from app.modules.inspections.models import (
    Inspection,
    InspectionActionItem,
    InspectionFinding,
)
from app.modules.inspections.schemas import (
    ActionItemRead,
    CreateActionItemRequest,
    CreateFindingRequest,
    CreateInspectionRequest,
    FindingRead,
    InspectionListItem,
    InspectionPage,
    InspectionRead,
    InspectionStats,
    UpdateActionItemRequest,
    UpdateInspectionRequest,
)
from app.modules.schools.models import School
from app.modules.workflow.models import AuditLog
from app.shared.enums import (
    ActionItemStatus,
    FindingSeverity,
    InspectionStatus,
)
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)


def _utc(d: Any) -> datetime:
    """Coerce a date or datetime to a tz-aware UTC datetime."""
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return datetime.combine(d, datetime.min.time(), tzinfo=UTC)


class InspectionsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.census = CensusService(session)

    # ==================================================================
    # PLAN + LIST
    # ==================================================================
    async def list_inspections(
        self,
        user: User,
        school_id: str | None,
        status: InspectionStatus | None,
        page: int,
        page_size: int,
    ) -> InspectionPage:
        page = max(1, page)
        page_size = max(1, min(500, page_size))

        base = (
            select(Inspection)
            .options(
                selectinload(Inspection.school),
                selectinload(Inspection.inspector),
            )
            .order_by(Inspection.scheduledDate.desc())
        )
        base = self._scope_query(base, user)
        if school_id:
            base = base.where(Inspection.schoolId == school_id)
        if status:
            base = base.where(Inspection.status == status)

        total = (
            await self.session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()

        rows = (
            await self.session.execute(
                base.offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars().all()

        # Per-row counts (small N because of pagination)
        ids = [r.id for r in rows]
        findings_by_insp: dict[str, int] = {}
        actions_open_by_insp: dict[str, int] = {}
        if ids:
            findings_rows = (
                await self.session.execute(
                    select(InspectionFinding.inspectionId, func.count())
                    .where(InspectionFinding.inspectionId.in_(ids))
                    .group_by(InspectionFinding.inspectionId)
                )
            ).all()
            findings_by_insp = {iid: int(n) for iid, n in findings_rows}
            actions_rows = (
                await self.session.execute(
                    select(InspectionActionItem.inspectionId, func.count())
                    .where(
                        InspectionActionItem.inspectionId.in_(ids),
                        InspectionActionItem.status.in_(
                            [ActionItemStatus.OPEN, ActionItemStatus.IN_PROGRESS]
                        ),
                    )
                    .group_by(InspectionActionItem.inspectionId)
                )
            ).all()
            actions_open_by_insp = {iid: int(n) for iid, n in actions_rows}

        items = [
            InspectionListItem(
                id=r.id,
                schoolId=r.schoolId,
                school={"id": r.school.id, "name": r.school.name, "code": r.school.code}
                if r.school else None,
                inspectorId=r.inspectorId,
                inspector={
                    "id": r.inspector.id, "fullName": r.inspector.fullName,
                    "email": r.inspector.email,
                } if r.inspector else None,
                scheduledDate=r.scheduledDate,
                performedDate=r.performedDate,
                status=r.status,
                overallScore=r.overallScore,
                findingsCount=findings_by_insp.get(r.id, 0),
                actionItemsOpen=actions_open_by_insp.get(r.id, 0),
            )
            for r in rows
        ]
        return InspectionPage(
            rows=items, total=total, page=page, pageSize=page_size
        )

    async def get(self, user: User, inspection_id: str) -> InspectionRead:
        insp = await self._load_full(inspection_id)
        await self._assert_can_read(user, insp)
        return self._map_full(insp)

    async def create(
        self, user: User, dto: CreateInspectionRequest
    ) -> InspectionRead:
        # Verify the school exists and is in scope
        school = await self.session.get(School, dto.schoolId)
        if school is None:
            raise NotFoundError(detail="École introuvable")
        await self.census.assert_can_access_school(user, dto.schoolId)

        inspector_id = dto.inspectorId or user.id
        inspector = await self.session.get(User, inspector_id)
        if inspector is None:
            raise NotFoundError(detail="Inspecteur introuvable")

        insp = Inspection(
            schoolId=dto.schoolId,
            inspectorId=inspector_id,
            scheduledDate=_utc(dto.scheduledDate),
            status=InspectionStatus.PLANNED,
            notes=dto.notes,
        )
        self.session.add(insp)
        await self.session.flush()

        self.session.add(AuditLog(
            actorId=user.id,
            action="CREATE_INSPECTION",
            entity="Inspection",
            entityId=insp.id,
            metadata_={"schoolId": dto.schoolId, "inspectorId": inspector_id},
        ))
        await self.session.flush()

        loaded = await self._load_full(insp.id)
        return self._map_full(loaded)

    async def update(
        self, user: User, inspection_id: str, dto: UpdateInspectionRequest
    ) -> InspectionRead:
        insp = await self._load_full(inspection_id)
        await self._assert_can_write(user, insp)

        if dto.status is not None:
            # Auto-fill performedDate when transitioning to COMPLETED
            if (
                dto.status == InspectionStatus.COMPLETED
                and insp.performedDate is None
                and dto.performedDate is None
            ):
                insp.performedDate = datetime.now(UTC)
            insp.status = dto.status
        if dto.performedDate is not None:
            insp.performedDate = _utc(dto.performedDate)
        if dto.notes is not None:
            insp.notes = dto.notes

        # Recompute overall score on every update if there are findings
        if insp.findings:
            insp.overallScore = self._score_from_findings(insp.findings)

        self.session.add(AuditLog(
            actorId=user.id,
            action="UPDATE_INSPECTION",
            entity="Inspection",
            entityId=insp.id,
            metadata_={"status": insp.status.value},
        ))
        await self.session.flush()
        # Re-load with relations so _map_full doesn't trigger lazy-raise
        # (flush expires attributes — including selectinload'd ones — by default)
        loaded = await self._load_full(insp.id)
        return self._map_full(loaded)

    # ==================================================================
    # FINDINGS
    # ==================================================================
    async def add_finding(
        self, user: User, inspection_id: str, dto: CreateFindingRequest
    ) -> FindingRead:
        insp = await self._load_full(inspection_id)
        await self._assert_can_write(user, insp)
        if insp.status == InspectionStatus.CANCELLED:
            raise ConflictError(detail="Inspection annulée — aucun ajout possible.")

        finding = InspectionFinding(
            inspectionId=insp.id,
            criterion=dto.criterion,
            score=dto.score,
            severity=dto.severity,
            comment=dto.comment,
            photoUrl=dto.photoUrl,
        )
        self.session.add(finding)
        await self.session.flush()

        # Refresh insp.findings to recompute score
        await self.session.refresh(insp, attribute_names=["findings"])
        insp.overallScore = self._score_from_findings(insp.findings)
        await self.session.flush()

        return FindingRead.model_validate(finding)

    # ==================================================================
    # ACTION ITEMS
    # ==================================================================
    async def add_action(
        self, user: User, inspection_id: str, dto: CreateActionItemRequest
    ) -> ActionItemRead:
        insp = await self._load_full(inspection_id)
        await self._assert_can_write(user, insp)

        action = InspectionActionItem(
            inspectionId=insp.id,
            description=dto.description,
            dueDate=_utc(dto.dueDate),
            status=ActionItemStatus.OPEN,
        )
        self.session.add(action)
        await self.session.flush()
        await self.session.refresh(action)
        return ActionItemRead.model_validate(action)

    async def update_action(
        self, user: User, action_id: str, dto: UpdateActionItemRequest
    ) -> ActionItemRead:
        action = await self.session.get(InspectionActionItem, action_id)
        if action is None:
            raise NotFoundError(detail="Action introuvable")

        # Verify scope through the parent inspection
        insp = await self._load_full(action.inspectionId)
        await self._assert_can_write(user, insp)

        action.status = dto.status
        if dto.status == ActionItemStatus.RESOLVED:
            action.resolvedAt = datetime.now(UTC)
            action.resolvedById = user.id
        else:
            action.resolvedAt = None
            action.resolvedById = None

        self.session.add(AuditLog(
            actorId=user.id,
            action="UPDATE_ACTION_ITEM",
            entity="InspectionActionItem",
            entityId=action.id,
            metadata_={
                "status": dto.status.value,
                "resolutionNote": dto.resolutionNote,
            },
        ))
        await self.session.flush()
        # Refresh to repopulate attributes expired by the flush
        await self.session.refresh(action)
        return ActionItemRead.model_validate(action)

    # ==================================================================
    # STATS (synthèse globale dans le scope du caller)
    # ==================================================================
    async def stats(self, user: User) -> InspectionStats:
        ninety_days_ago = datetime.now(UTC) - timedelta(days=90)

        scoped = self._scope_query(select(Inspection.id), user).subquery()
        scoped_ids = select(scoped.c.id)

        async def _count_status(s: InspectionStatus) -> int:
            return (await self.session.execute(
                select(func.count()).select_from(Inspection).where(
                    Inspection.id.in_(scoped_ids),
                    Inspection.status == s,
                )
            )).scalar_one()

        total = (await self.session.execute(
            select(func.count()).select_from(Inspection).where(
                Inspection.id.in_(scoped_ids)
            )
        )).scalar_one()
        planned = await _count_status(InspectionStatus.PLANNED)
        in_progress = await _count_status(InspectionStatus.IN_PROGRESS)
        completed = await _count_status(InspectionStatus.COMPLETED)
        cancelled = await _count_status(InspectionStatus.CANCELLED)

        avg_score = (await self.session.execute(
            select(func.avg(Inspection.overallScore)).where(
                Inspection.id.in_(scoped_ids),
                Inspection.status == InspectionStatus.COMPLETED,
                Inspection.performedDate >= ninety_days_ago,
            )
        )).scalar_one()
        critical = (await self.session.execute(
            select(func.count()).select_from(InspectionFinding).where(
                InspectionFinding.inspectionId.in_(scoped_ids),
                InspectionFinding.severity == FindingSeverity.CRITICAL,
                InspectionFinding.createdAt >= ninety_days_ago,
            )
        )).scalar_one()
        overdue = (await self.session.execute(
            select(func.count()).select_from(InspectionActionItem).where(
                InspectionActionItem.inspectionId.in_(scoped_ids),
                InspectionActionItem.status.in_(
                    [ActionItemStatus.OPEN, ActionItemStatus.IN_PROGRESS]
                ),
                InspectionActionItem.dueDate < datetime.now(UTC),
            )
        )).scalar_one()

        return InspectionStats(
            total=total,
            planned=planned,
            inProgress=in_progress,
            completed=completed,
            cancelled=cancelled,
            averageScoreLast90Days=float(avg_score) if avg_score is not None else None,
            criticalFindingsLast90Days=int(critical),
            overdueActions=int(overdue),
        )

    # ==================================================================
    # HELPERS
    # ==================================================================
    async def _load_full(self, inspection_id: str) -> Inspection:
        insp = (await self.session.execute(
            select(Inspection)
            .where(Inspection.id == inspection_id)
            .options(
                selectinload(Inspection.school),
                selectinload(Inspection.inspector),
                selectinload(Inspection.findings),
                selectinload(Inspection.actionItems),
            )
        )).scalar_one_or_none()
        if insp is None:
            raise NotFoundError(detail="Inspection introuvable")
        return insp

    async def _assert_can_read(self, user: User, insp: Inspection) -> None:
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        if user.id == insp.inspectorId:
            return
        await self.census.assert_can_access_school(user, insp.schoolId)

    async def _assert_can_write(self, user: User, insp: Inspection) -> None:
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        if user.id == insp.inspectorId:
            return
        # Allow validation roles (regional/prefecture/sub-prefecture) on schools in scope
        if user.role in (
            *REGIONAL_SCOPE_ROLES, *PREFECTURE_SCOPE_ROLES, *SUB_PREFECTURE_SCOPE_ROLES
        ):
            await self.census.assert_can_access_school(user, insp.schoolId)
            return
        raise ForbiddenError(detail="Modification non autorisée pour cette inspection.")

    def _scope_query(self, stmt: Any, user: User) -> Any:
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        scoped_school_ids = self._scoped_school_ids_subq(user)
        return stmt.where(Inspection.schoolId.in_(scoped_school_ids))

    @staticmethod
    def _scoped_school_ids_subq(user: User) -> Any:
        stmt = select(School.id)
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(School.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(School.prefectureId == user.prefectureId)
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(School.subPrefectureId == user.subPrefectureId)
        if user.schoolId:
            return stmt.where(School.id == user.schoolId)
        return stmt.where(School.id == "__none__")

    @staticmethod
    def _score_from_findings(findings: list[InspectionFinding]) -> float:
        """Score 0-100 = moyenne des scores 0-5 × 20.

        Pondération sévérité : CRITICAL pèse 3×, MAJOR 2×, MINOR 1.5×, INFO 1×.
        """
        if not findings:
            return 0.0
        weights = {
            FindingSeverity.INFO: 1.0,
            FindingSeverity.MINOR: 1.5,
            FindingSeverity.MAJOR: 2.0,
            FindingSeverity.CRITICAL: 3.0,
        }
        weighted_sum = sum(f.score * weights[f.severity] for f in findings)
        weight_total = sum(weights[f.severity] for f in findings)
        return round((weighted_sum / weight_total) * 20, 1)

    @staticmethod
    def _map_full(insp: Inspection) -> InspectionRead:
        return InspectionRead(
            id=insp.id,
            schoolId=insp.schoolId,
            school={"id": insp.school.id, "name": insp.school.name, "code": insp.school.code}
            if insp.school else None,
            inspectorId=insp.inspectorId,
            inspector={
                "id": insp.inspector.id, "fullName": insp.inspector.fullName,
                "email": insp.inspector.email,
            } if insp.inspector else None,
            scheduledDate=insp.scheduledDate,
            performedDate=insp.performedDate,
            status=insp.status,
            overallScore=insp.overallScore,
            notes=insp.notes,
            findings=[FindingRead.model_validate(f) for f in insp.findings],
            actionItems=[ActionItemRead.model_validate(a) for a in insp.actionItems],
            createdAt=insp.createdAt,
            updatedAt=insp.updatedAt,
        )
