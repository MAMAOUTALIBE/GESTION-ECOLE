"""Module 5C — Routeur HTTP de l'audit PII.

Endpoints
---------
* ``GET  /api/pii-audit/logs``                       — listing (REGIONAL+).
* ``GET  /api/pii-audit/history/{entityType}/{id}``  — historique d'une
  entité (NATIONAL / MINISTRY only).
* ``POST /api/pii-audit/purge``                      — purge (NATIONAL only).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.pii_audit.enums import PiiAccessType, PiiEntityType
from app.modules.pii_audit.schemas import (
    PiiAccessLogEntry,
    PiiAccessLogFilters,
    PurgeRequest,
    PurgeResponse,
)
from app.modules.pii_audit.service import PiiAuditService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

# Rôles autorisés à interroger les logs (au-delà de leurs propres
# accès) : REGIONAL_ADMIN+ — pour qu'un IRE puisse répondre à un parent
# inquiet. Les autres rôles peuvent toujours appeler l'endpoint mais
# n'auront que LEURS propres accès en retour (filtre RBAC du service).
LIST_LOGS_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.INSPECTOR,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
)

HISTORY_ROLES = (UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN)
PURGE_ROLES = (UserRole.NATIONAL_ADMIN,)


def _service(session: DbSession) -> PiiAuditService:
    return PiiAuditService(session)


Svc = Annotated[PiiAuditService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["pii-audit"])


# ---------------------------------------------------------------------------
# GET /logs — listing filtrable (RBAC scope appliqué par le service)
# ---------------------------------------------------------------------------
@router.get(
    "/logs",
    response_model=list[PiiAccessLogEntry],
    dependencies=[Depends(require_roles(*LIST_LOGS_ROLES))],
    summary="Liste les accès PII consignés (RBAC scope appliqué).",
)
async def list_logs(
    user: CurrentUserDep,
    service: Svc,
    entityType: Annotated[PiiEntityType | None, Query()] = None,
    entityId: Annotated[str | None, Query(max_length=30)] = None,
    userId: Annotated[str | None, Query(max_length=30)] = None,
    accessType: Annotated[PiiAccessType | None, Query()] = None,
    fromDate: Annotated[str | None, Query()] = None,
    toDate: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PiiAccessLogEntry]:
    filters = PiiAccessLogFilters(
        entityType=entityType,
        entityId=entityId,
        userId=userId,
        accessType=accessType,
        fromDate=fromDate,  # type: ignore[arg-type]
        toDate=toDate,  # type: ignore[arg-type]
        limit=limit,
        offset=offset,
    )
    return await service.list_accesses(filters, user)


# ---------------------------------------------------------------------------
# GET /history/{entityType}/{entityId} — NATIONAL / MINISTRY ADMIN ONLY
# ---------------------------------------------------------------------------
@router.get(
    "/history/{entity_type}/{entity_id}",
    response_model=list[PiiAccessLogEntry],
    dependencies=[Depends(require_roles(*HISTORY_ROLES))],
    summary="Historique des accès sur une entité (admins nationaux).",
)
async def history_for_entity(
    entity_type: PiiEntityType,
    entity_id: str,
    user: CurrentUserDep,
    service: Svc,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[PiiAccessLogEntry]:
    return await service.get_history_for_entity(
        entity_type, entity_id, user, limit=limit,
    )


# ---------------------------------------------------------------------------
# POST /purge — NATIONAL ADMIN ONLY
# ---------------------------------------------------------------------------
@router.post(
    "/purge",
    response_model=PurgeResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*PURGE_ROLES))],
    summary="Purge des entrées plus anciennes que cutoffDate (NATIONAL_ADMIN).",
)
async def purge_logs(
    payload: PurgeRequest,
    user: CurrentUserDep,
    service: Svc,
) -> PurgeResponse:
    deleted = await service.purge_old_logs(payload.cutoffDate, user)
    return PurgeResponse(deleted=deleted, cutoffDate=payload.cutoffDate)


__all__ = ["router"]
