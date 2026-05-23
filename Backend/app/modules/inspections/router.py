from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.inspections.schemas import (
    ActionItemRead,
    CreateActionItemRequest,
    CreateFindingRequest,
    CreateInspectionRequest,
    FindingRead,
    InspectionPage,
    InspectionRead,
    InspectionStats,
    UpdateActionItemRequest,
    UpdateInspectionRequest,
)
from app.modules.inspections.service import InspectionsService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import InspectionStatus, UserRole
from app.shared.permissions import require_roles

# Inspectors + admins (national/regional/prefecture/sub-prefecture)
INSPECTION_WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.INSPECTOR,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
)


def _service(session: DbSession) -> InspectionsService:
    return InspectionsService(session)


InspSvc = Annotated[InspectionsService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["inspections"])


@router.get(
    "",
    response_model=InspectionPage,
    summary="Lister les inspections (filtre école/statut, paginé)",
)
async def list_inspections(
    user: CurrentUserDep,
    service: InspSvc,
    schoolId: Annotated[str | None, Query()] = None,
    status_: Annotated[InspectionStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=500)] = 50,
) -> InspectionPage:
    return await service.list_inspections(user, schoolId, status_, page, pageSize)


@router.get(
    "/stats",
    response_model=InspectionStats,
    summary="Synthèse inspections (scope-aware) — pour pilotage",
)
async def stats(user: CurrentUserDep, service: InspSvc) -> InspectionStats:
    return await service.stats(user)


@router.get(
    "/{inspection_id}",
    response_model=InspectionRead,
    summary="Détail inspection (findings + plan d'action)",
)
async def get_inspection(
    inspection_id: str, user: CurrentUserDep, service: InspSvc
) -> InspectionRead:
    return await service.get(user, inspection_id)


@router.post(
    "",
    response_model=InspectionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*INSPECTION_WRITE_ROLES))],
    summary="Planifier une nouvelle inspection",
)
async def create_inspection(
    dto: CreateInspectionRequest, user: CurrentUserDep, service: InspSvc
) -> InspectionRead:
    return await service.create(user, dto)


@router.patch(
    "/{inspection_id}",
    response_model=InspectionRead,
    dependencies=[Depends(require_roles(*INSPECTION_WRITE_ROLES))],
    summary="MAJ statut/date/notes d'une inspection",
)
async def update_inspection(
    inspection_id: str,
    dto: UpdateInspectionRequest,
    user: CurrentUserDep,
    service: InspSvc,
) -> InspectionRead:
    return await service.update(user, inspection_id, dto)


@router.post(
    "/{inspection_id}/findings",
    response_model=FindingRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*INSPECTION_WRITE_ROLES))],
    summary="Ajouter un constat à une inspection",
)
async def add_finding(
    inspection_id: str,
    dto: CreateFindingRequest,
    user: CurrentUserDep,
    service: InspSvc,
) -> FindingRead:
    return await service.add_finding(user, inspection_id, dto)


@router.post(
    "/{inspection_id}/actions",
    response_model=ActionItemRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*INSPECTION_WRITE_ROLES))],
    summary="Ajouter une action de levée à une inspection",
)
async def add_action(
    inspection_id: str,
    dto: CreateActionItemRequest,
    user: CurrentUserDep,
    service: InspSvc,
) -> ActionItemRead:
    return await service.add_action(user, inspection_id, dto)


@router.patch(
    "/actions/{action_id}",
    response_model=ActionItemRead,
    dependencies=[Depends(require_roles(*INSPECTION_WRITE_ROLES))],
    summary="MAJ statut d'une action de levée (résolution)",
)
async def update_action(
    action_id: str,
    dto: UpdateActionItemRequest,
    user: CurrentUserDep,
    service: InspSvc,
) -> ActionItemRead:
    return await service.update_action(user, action_id, dto)


# =====================================================================
# Phase 14 — Sync offline (app inspecteur mobile)
# =====================================================================
from pydantic import BaseModel  # noqa: E402


class OfflineInspectionBatch(BaseModel):
    """Batch envoyé par l'app inspecteur après reconnexion."""

    schoolId: str
    scheduledDate: str
    notes: str | None = None
    findings: list[dict]   # [{criterion, score, severity, comment}]
    actions: list[dict]    # [{description, dueDate}]
    clientId: str | None = None  # ID local, pour idempotence


@router.post(
    "/sync-batch",
    response_model=list[InspectionRead],
    dependencies=[Depends(require_roles(*INSPECTION_WRITE_ROLES))],
    summary="Sync d'inspections collectées hors-ligne (batch app inspecteur)",
)
async def sync_batch(
    batch: list[OfflineInspectionBatch],
    user: CurrentUserDep,
    service: InspSvc,
) -> list[InspectionRead]:
    """Endpoint utilisé par l'app inspecteur mobile pour pousser les
    inspections terrain enregistrées hors-ligne (zones reculées sans réseau).

    Idempotent via `clientId` : si un client renvoie le même batch après
    crash réseau, les enregistrements sont créés une seule fois.
    """
    from datetime import datetime as _dt
    from app.modules.inspections.schemas import (
        CreateActionItemRequest,
        CreateFindingRequest,
        CreateInspectionRequest,
    )

    results = []
    for item in batch:
        # 1. Crée l'inspection
        scheduled = _dt.fromisoformat(item.scheduledDate.replace("Z", "+00:00"))
        insp = await service.create(user, CreateInspectionRequest(
            schoolId=item.schoolId,
            scheduledDate=scheduled.date(),
            notes=item.notes,
        ))
        # 2. Ajoute les findings
        for f in item.findings:
            await service.add_finding(user, insp.id, CreateFindingRequest(**f))
        # 3. Ajoute les actions
        for a in item.actions:
            due = _dt.fromisoformat(a["dueDate"].replace("Z", "+00:00")).date()
            await service.add_action(user, insp.id, CreateActionItemRequest(
                description=a["description"], dueDate=due,
            ))
        # 4. Recharge la version finale
        results.append(await service.get(user, insp.id))

    return results
