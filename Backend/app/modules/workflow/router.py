"""Workflow router — exposes /api/validation-requests/* and /api/notifications/*.

The NestJS controller mounts these endpoints at the root (no per-controller
prefix), so they are wired in main.py with ``prefix=settings.api_prefix``
(no extra segment) — see app/main.py.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.modules.auth.models import User
from app.modules.workflow.schemas import (
    NotificationRead,
    ReviewValidationRequestPayload,
    UnreadCountResponse,
    ValidationRequestRead,
)
from app.modules.workflow.service import WorkflowService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import ValidationStatus


def _service(session: DbSession) -> WorkflowService:
    return WorkflowService(session)


WfSvc = Annotated[WorkflowService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["workflow"])


# --- Validation requests --------------------------------------------
@router.get(
    "/validation-requests",
    response_model=list[ValidationRequestRead],
    summary="Lister les demandes de validation (scope hiérarchique)",
)
async def list_validation_requests(
    user: CurrentUserDep,
    service: WfSvc,
    status: Annotated[ValidationStatus | None, Query()] = None,
) -> list[ValidationRequestRead]:
    return await service.list_validation_requests(user, status)


@router.patch(
    "/validation-requests/{request_id}/review",
    response_model=ValidationRequestRead,
    summary="Approuver / rejeter une demande de validation",
)
async def review_validation_request(
    request_id: str,
    dto: ReviewValidationRequestPayload,
    user: CurrentUserDep,
    service: WfSvc,
) -> ValidationRequestRead:
    return await service.review(user, request_id, dto.status, dto.reason)


# --- Notifications ---------------------------------------------------
@router.get(
    "/notifications",
    response_model=list[NotificationRead],
    summary="Lister les notifications de l'utilisateur (max 100)",
)
async def list_notifications(
    user: CurrentUserDep,
    service: WfSvc,
    unreadOnly: Annotated[str | None, Query()] = None,
) -> list[NotificationRead]:
    return await service.notifications(user, unreadOnly == "true")


@router.get(
    "/notifications/unread-count",
    response_model=UnreadCountResponse,
    summary="Compter les notifications non lues",
)
async def unread_count(user: CurrentUserDep, service: WfSvc) -> UnreadCountResponse:
    return await service.unread_count(user)


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=NotificationRead,
    summary="Marquer une notification comme lue",
)
async def mark_notification_read(
    notification_id: str, user: CurrentUserDep, service: WfSvc
) -> NotificationRead:
    return await service.mark_notification_read(user, notification_id)
