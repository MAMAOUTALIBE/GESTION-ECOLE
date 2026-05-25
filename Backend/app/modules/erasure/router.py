"""Module 5D — Router HTTP du droit à l'oubli.

Endpoints
---------
* ``POST /api/erasure/requests``                       — création
* ``GET  /api/erasure/requests``                       — listing filtré
* ``GET  /api/erasure/requests/{id}``                  — détail
* ``POST /api/erasure/requests/{id}/cancel``           — annulation
* ``POST /api/erasure/execute-pending``                — batch (NATIONAL only)
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.erasure.enums import ErasureStatus
from app.modules.erasure.schemas import (
    CancelErasureRequest,
    ErasureRequestCreate,
    ErasureRequestRead,
    ExecutePendingResponse,
)
from app.modules.erasure.service import (
    ERASURE_ADMIN_ROLES,
    ERASURE_EXECUTE_ROLES,
    ErasureService,
)
from app.shared.deps import DbSession, get_current_user
from app.shared.permissions import require_roles


def _service(session: DbSession) -> ErasureService:
    return ErasureService(session)


Svc = Annotated[ErasureService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["erasure"])


# ---------------------------------------------------------------------------
# POST /requests — création d'une demande de droit à l'oubli
# ---------------------------------------------------------------------------
@router.post(
    "/requests",
    response_model=ErasureRequestRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*ERASURE_ADMIN_ROLES))],
    summary="Crée une demande de droit à l'oubli (status=GRACE_PERIOD).",
)
async def create_request(
    payload: ErasureRequestCreate,
    user: CurrentUserDep,
    service: Svc,
) -> ErasureRequestRead:
    return await service.request_erasure(payload, user)


# ---------------------------------------------------------------------------
# GET /requests — listing filtrable par statut
# ---------------------------------------------------------------------------
@router.get(
    "/requests",
    response_model=list[ErasureRequestRead],
    dependencies=[Depends(require_roles(*ERASURE_ADMIN_ROLES))],
    summary="Liste les demandes (filtre par statut).",
)
async def list_requests(
    user: CurrentUserDep,
    service: Svc,
    erasure_status: Annotated[
        ErasureStatus | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ErasureRequestRead]:
    return await service.list_pending_erasures(
        user,
        status=erasure_status,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /requests/{id} — détail
# ---------------------------------------------------------------------------
@router.get(
    "/requests/{erasure_id}",
    response_model=ErasureRequestRead,
    dependencies=[Depends(require_roles(*ERASURE_ADMIN_ROLES))],
    summary="Détail d'une demande de droit à l'oubli.",
)
async def get_request(
    erasure_id: str,
    user: CurrentUserDep,
    service: Svc,
) -> ErasureRequestRead:
    return await service.get_erasure(erasure_id, user)


# ---------------------------------------------------------------------------
# POST /requests/{id}/cancel — annulation pendant la grace period
# ---------------------------------------------------------------------------
@router.post(
    "/requests/{erasure_id}/cancel",
    response_model=ErasureRequestRead,
    dependencies=[Depends(require_roles(*ERASURE_ADMIN_ROLES))],
    summary="Annule une demande pendant la grace period.",
)
async def cancel_request(
    erasure_id: str,
    payload: CancelErasureRequest,
    user: CurrentUserDep,
    service: Svc,
) -> ErasureRequestRead:
    return await service.cancel_erasure(erasure_id, payload, user)


# ---------------------------------------------------------------------------
# POST /execute-pending — batch (NATIONAL only)
# ---------------------------------------------------------------------------
@router.post(
    "/execute-pending",
    response_model=ExecutePendingResponse,
    dependencies=[Depends(require_roles(*ERASURE_EXECUTE_ROLES))],
    summary="Exécute toutes les demandes éligibles (NATIONAL_ADMIN only).",
)
async def execute_pending(
    user: CurrentUserDep,
    service: Svc,
) -> ExecutePendingResponse:
    result = await service.execute_pending_erasures(user)
    return ExecutePendingResponse(
        executed=result["executed"],
        skipped=result["skipped"],
    )


__all__ = ["router"]
