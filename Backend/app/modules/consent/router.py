"""Module 5B — Router HTTP du consentement utilisateur.

Endpoints
---------
* ``GET  /api/consent/status``  — toute personne authentifiée.
* ``POST /api/consent/accept``  — toute personne authentifiée.

Aucun ``require_roles`` : la lecture et l'acceptation du consentement
sont des droits individuels qui ne dépendent pas du rôle métier.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.modules.auth.models import User
from app.modules.consent.schemas import AcceptConsentRequest, ConsentStatus
from app.modules.consent.service import ConsentService
from app.shared.deps import DbSession, get_current_user


def _service(session: DbSession) -> ConsentService:
    return ConsentService(session)


Svc = Annotated[ConsentService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["consent"])


# ---------------------------------------------------------------------------
# GET /status — statut du consentement pour l'utilisateur courant
# ---------------------------------------------------------------------------
@router.get(
    "/status",
    response_model=ConsentStatus,
    status_code=status.HTTP_200_OK,
    summary="Retourne la version requise + l'état du consentement utilisateur.",
)
async def get_status(
    user: CurrentUserDep,
    service: Svc,
) -> ConsentStatus:
    return await service.get_status(user)


# ---------------------------------------------------------------------------
# POST /accept — enregistre l'acceptation de la version courante
# ---------------------------------------------------------------------------
@router.post(
    "/accept",
    response_model=ConsentStatus,
    status_code=status.HTTP_200_OK,
    summary="Persiste l'acceptation du consentement (IP + UA tracés).",
)
async def accept(
    payload: AcceptConsentRequest,
    user: CurrentUserDep,
    service: Svc,
    request: Request,
) -> ConsentStatus:
    return await service.accept(user, payload, request)


__all__ = ["router"]
