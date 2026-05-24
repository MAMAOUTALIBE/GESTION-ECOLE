"""Module 11 — Diplômes signés numériquement (Ed25519).

Endpoints :

* ``POST   /api/diplomas``                    — émission (MINISTRY_ADMIN+).
* ``GET    /api/diplomas``                    — listing avec scope territorial
  (SCHOOL_DIRECTOR+).
* ``GET    /api/diplomas/verify/{serial}``    — **PUBLIC (sans auth)**.
* ``GET    /api/diplomas/{serial}/pdf``       — download PDF (SCHOOL_DIRECTOR+
  si owner / NATIONAL/MINISTRY sinon).
* ``POST   /api/diplomas/{serial}/revoke``    — révocation (NATIONAL_ADMIN).

Le router est volontairement compact : toute la logique métier vit dans
``DiplomaService``.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import select

from app.core.exceptions import NotFoundError
from app.modules.auth.models import User
from app.modules.diplomas.enums import DiplomaStatus, DiplomaType
from app.modules.diplomas.models import Diploma
from app.modules.diplomas.schemas import (
    DiplomaIssueRequest,
    DiplomaListResponse,
    DiplomaRead,
    DiplomaRevokeRequest,
    DiplomaVerification,
)
from app.modules.diplomas.service import DiplomaService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import (
    require_roles,
)

router = APIRouter(tags=["diplomas"])


ISSUE_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
)
READ_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.INSPECTOR,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
)
REVOKE_ROLES = (UserRole.NATIONAL_ADMIN,)


def _svc(session: DbSession) -> DiplomaService:
    return DiplomaService(session)


Svc = Annotated[DiplomaService, Depends(_svc)]


# ===========================================================================
# 1. POST /api/diplomas — issue (MINISTRY_ADMIN+)
# ===========================================================================
@router.post(
    "",
    response_model=DiplomaRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*ISSUE_ROLES))],
    summary="Émet un nouveau diplôme signé numériquement",
)
async def issue_diploma(
    payload: DiplomaIssueRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
) -> DiplomaRead:
    diploma = await service.issue_diploma(
        student_id=payload.studentId,
        diploma_type=payload.diplomaType,
        school_id=payload.schoolId,
        actor=user,
        academic_year_id=payload.academicYearId,
        exam_center=payload.examCenter,
        score=payload.score,
        mention=payload.mention,
    )
    return DiplomaRead.model_validate(diploma)


# ===========================================================================
# 2. GET /api/diplomas — list (scope territorial)
# ===========================================================================
@router.get(
    "",
    response_model=DiplomaListResponse,
    dependencies=[Depends(require_roles(*READ_ROLES))],
    summary="Liste les diplômes accessibles à l'utilisateur courant",
)
async def list_diplomas(
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
    status_filter: Annotated[
        DiplomaStatus | None, Query(alias="status"),
    ] = None,
    schoolId: Annotated[str | None, Query()] = None,
    diplomaType: Annotated[DiplomaType | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DiplomaListResponse:
    items, total = await service.list_diplomas(
        actor=user,
        status_filter=status_filter,
        school_id=schoolId,
        diploma_type=diplomaType,
        limit=limit,
        offset=offset,
    )
    return DiplomaListResponse(
        items=[DiplomaRead.model_validate(d) for d in items],
        total=total,
    )


# ===========================================================================
# 3. GET /api/diplomas/verify/{serial} — PUBLIC sans auth
# ===========================================================================
@router.get(
    "/verify/{serial}",
    response_model=DiplomaVerification,
    summary="Vérification PUBLIQUE d'un diplôme (sans authentification)",
)
async def verify_diploma(
    serial: Annotated[str, Path(min_length=6, max_length=40)],
    service: Svc,
) -> DiplomaVerification:
    """Endpoint **PUBLIC** : aucune authentification requise.

    Retourne :

    * ``VALID``    — diplôme ISSUED, signature recompute correctement.
      Le payload signé + la signature sont inclus pour permettre une
      vérification offline avec la clé publique distribuée.
    * ``REVOKED``  — diplôme révoqué, avec la raison publique.
    * HTTP 404 si serial inconnu — body structuré ``{status: NOT_FOUND}``.

    Anti-énumération : la 404 ne distingue pas "format invalide" vs
    "n'existe pas vraiment".
    """
    try:
        return await service.verify_diploma(serial)
    except NotFoundError:
        # On renvoie 404 mais avec un body conforme au schema pour que
        # le frontend puisse parser uniformément.
        return Response(  # type: ignore[return-value]
            content=DiplomaVerification(
                status="NOT_FOUND", serial=serial,
            ).model_dump_json(),
            media_type="application/json",
            status_code=status.HTTP_404_NOT_FOUND,
        )


# ===========================================================================
# 4. GET /api/diplomas/{serial}/pdf — download
# ===========================================================================
@router.get(
    "/{serial}/pdf",
    dependencies=[Depends(require_roles(*READ_ROLES))],
    summary="Télécharge le PDF officiel d'un diplôme (si disponible)",
)
async def download_diploma_pdf(
    serial: Annotated[str, Path(min_length=6, max_length=40)],
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
    session: DbSession,
) -> Response:
    # Vérification d'ownership : un SCHOOL_DIRECTOR ne télécharge que les
    # diplômes de SON école. Les rôles régionaux+ voient toute leur zone
    # (cf. RBAC dans le service).
    diploma = (await session.execute(
        select(Diploma).where(Diploma.serial == serial),
    )).scalar_one_or_none()
    if diploma is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diplôme introuvable.",
        )

    if (
        user.role == UserRole.SCHOOL_DIRECTOR
        and diploma.schoolId != user.schoolId
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diplôme appartenant à une autre école.",
        )

    pdf_bytes = await service.get_diploma_pdf(serial)
    if pdf_bytes is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "PDF non encore généré pour ce diplôme. La signature "
                "reste vérifiable via /verify/{serial}."
            ),
        )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="diplome-{serial}.pdf"',
        },
    )


# ===========================================================================
# 5. POST /api/diplomas/{serial}/revoke — revoke (NATIONAL_ADMIN)
# ===========================================================================
@router.post(
    "/{serial}/revoke",
    response_model=DiplomaRead,
    dependencies=[Depends(require_roles(*REVOKE_ROLES))],
    summary="Révoque un diplôme (administrateur national uniquement)",
)
async def revoke_diploma(
    serial: Annotated[str, Path(min_length=6, max_length=40)],
    payload: DiplomaRevokeRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
) -> DiplomaRead:
    diploma = await service.revoke_diploma(serial, payload.reason, user)
    return DiplomaRead.model_validate(diploma)
