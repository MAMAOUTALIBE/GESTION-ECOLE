from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.notifications.schemas import (
    BulkCommunicationRequest,
    BulkCommunicationResponse,
    CommunicationRead,
    CreateCommunicationRequest,
    DispatchTestRequest,
    DispatchTestResponse,
)
from app.modules.notifications.service import NotificationsService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import CommunicationStatus, UserRole
from app.shared.permissions import require_roles

# Anyone with academic write rights can compose a parent communication
COMMUNICATION_WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
    UserRole.TEACHER,
    UserRole.CENSUS_AGENT,
)
TEST_DISPATCH_ROLES = (UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN)


def _service(session: DbSession) -> NotificationsService:
    return NotificationsService(session)


NotifSvc = Annotated[NotificationsService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["notifications"])


@router.get(
    "/communications",
    response_model=list[CommunicationRead],
    summary="Lister les communications parents (filtres parent/élève/statut)",
)
async def list_communications(
    user: CurrentUserDep,
    service: NotifSvc,
    parentId: Annotated[str | None, Query()] = None,
    studentId: Annotated[str | None, Query()] = None,
    status_: Annotated[CommunicationStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[CommunicationRead]:
    return await service.list_communications(
        user, parentId, studentId, status_, limit
    )


@router.get(
    "/communications/{communication_id}",
    response_model=CommunicationRead,
    summary="Détail d'une communication parent",
)
async def get_communication(
    communication_id: str, user: CurrentUserDep, service: NotifSvc
) -> CommunicationRead:
    _ = user
    return await service.get(communication_id)


@router.post(
    "/communications",
    response_model=CommunicationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*COMMUNICATION_WRITE_ROLES))],
    summary="Créer une communication parent (éventuellement queue immédiat)",
)
async def create_communication(
    dto: CreateCommunicationRequest, user: CurrentUserDep, service: NotifSvc
) -> CommunicationRead:
    return await service.create(user, dto)


@router.post(
    "/communications/bulk",
    response_model=BulkCommunicationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*COMMUNICATION_WRITE_ROLES))],
    summary="Envoyer en masse à plusieurs parents (queue Celery)",
)
async def create_bulk_communication(
    dto: BulkCommunicationRequest, user: CurrentUserDep, service: NotifSvc
) -> BulkCommunicationResponse:
    return await service.create_bulk(user, dto)


@router.post(
    "/communications/{communication_id}/retry",
    response_model=CommunicationRead,
    dependencies=[Depends(require_roles(*COMMUNICATION_WRITE_ROLES))],
    summary="Re-tenter une communication FAILED ou DRAFT",
)
async def retry_communication(
    communication_id: str, user: CurrentUserDep, service: NotifSvc
) -> CommunicationRead:
    return await service.retry(user, communication_id)


@router.post(
    "/communications/test",
    response_model=DispatchTestResponse,
    dependencies=[Depends(require_roles(*TEST_DISPATCH_ROLES))],
    summary="Test ad-hoc d'un canal (national admins) — bypass DB",
)
async def dispatch_test(
    dto: DispatchTestRequest, user: CurrentUserDep, service: NotifSvc
) -> DispatchTestResponse:
    _ = user
    return await service.dispatch_test(dto)
