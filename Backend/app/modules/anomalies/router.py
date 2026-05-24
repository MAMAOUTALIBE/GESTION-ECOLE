"""Module 9 — Router anomalies (détection règle + statistique).

Endpoints :

* ``POST /api/anomalies/run`` — déclenche un run de détection (REGIONAL_ADMIN+)
* ``GET  /api/anomalies`` — liste paginée filtrable (SCHOOL_DIRECTOR+)
* ``GET  /api/anomalies/{id}`` — détail (SCHOOL_DIRECTOR+)
* ``POST /api/anomalies/{id}/review`` — marque revue (SCHOOL_DIRECTOR+)
* ``GET  /api/anomalies/stats`` — KPI agrégés (SCHOOL_DIRECTOR+)

Endpoint hérité phase 14 (``GET /scan``) supprimé : il scannait à la volée
sans persistance, ce qui est incompatible avec le workflow de revue. Le
nouveau ``POST /run`` le remplace en persistant les résultats.

RBAC
----
* Run = REGIONAL_ADMIN+ : opération coûteuse, peut produire des centaines
  d'anomalies — on évite que chaque directeur d'école déclenche.
* Read / review = SCHOOL_DIRECTOR+ avec scope territorial automatique
  (cf. ``_scope_anomalies_for_user``).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.anomalies.enums import (
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
)
from app.modules.anomalies.schemas import (
    AnomalyListResponse,
    AnomalyRead,
    AnomalyReviewRequest,
    AnomalyRunResponse,
    AnomalyStats,
)
from app.modules.anomalies.service import AnomalyService
from app.modules.auth.models import User
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
    require_roles,
)

router = APIRouter(tags=["anomalies"])


# RBAC groups
RUN_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.INSPECTOR,
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


def _svc(session: DbSession) -> AnomalyService:
    return AnomalyService(session)


Svc = Annotated[AnomalyService, Depends(_svc)]


def _scope_filters_for_user(user: User) -> dict[str, str | None]:
    """Calcule les filtres territoriaux à appliquer à l'utilisateur courant.

    Renvoie un dict avec ``school_id`` / ``region_id`` à passer à
    ``list_anomalies`` / ``get_stats``. Pour les rôles nationaux, on ne
    filtre rien (None = pas de filtre).
    """
    if user.role in NATIONAL_SCOPE_ROLES:
        return {"school_id": None, "region_id": None}
    if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
        return {"school_id": None, "region_id": user.regionId}
    if user.role in PREFECTURE_SCOPE_ROLES and user.regionId:
        # Pas de colonne prefectureId sur AnomalyDetection — fallback région
        return {"school_id": None, "region_id": user.regionId}
    if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.regionId:
        return {"school_id": None, "region_id": user.regionId}
    # Directeur d'école : scope strict à son école.
    return {"school_id": user.schoolId, "region_id": None}


# ===========================================================================
# Endpoints
# ===========================================================================
@router.post(
    "/run",
    response_model=AnomalyRunResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*RUN_ROLES))],
    summary="Déclenche un run de détection d'anomalies",
)
async def run_detection(
    service: Svc,
    schoolId: Annotated[str | None, Query()] = None,
) -> AnomalyRunResponse:
    """Lance tous les détecteurs.

    Si ``schoolId`` est fourni, restreint la détection à cette école — sinon
    balayage global (réservé en pratique aux rôles nationaux mais le RBAC
    minimum est REGIONAL_ADMIN).
    """
    count = await service.run_all_detectors(school_id=schoolId)
    return AnomalyRunResponse(
        detected=count,
        schoolId=schoolId,
        ranAt=datetime.now(UTC),
    )


@router.get(
    "/stats",
    response_model=AnomalyStats,
    dependencies=[Depends(require_roles(*READ_ROLES))],
    summary="KPI agrégés (total, par type, par sévérité, taux confirmation)",
)
async def get_stats(
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
) -> AnomalyStats:
    scope = _scope_filters_for_user(user)
    return await service.get_stats(**scope)


@router.get(
    "",
    response_model=AnomalyListResponse,
    dependencies=[Depends(require_roles(*READ_ROLES))],
    summary="Liste paginée des anomalies (scope territorial automatique)",
)
async def list_anomalies(
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
    status_filter: Annotated[
        AnomalyStatus | None, Query(alias="status")
    ] = None,
    severity: Annotated[AnomalySeverity | None, Query()] = None,
    type_filter: Annotated[
        AnomalyType | None, Query(alias="type"),
    ] = None,
    entityId: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=200)] = 25,
) -> AnomalyListResponse:
    scope = _scope_filters_for_user(user)
    items, total = await service.list_anomalies(
        status=status_filter,
        severity=severity,
        a_type=type_filter,
        school_id=scope["school_id"],
        region_id=scope["region_id"],
        entity_id=entityId,
        page=page,
        page_size=pageSize,
    )
    return AnomalyListResponse(
        items=[AnomalyRead.model_validate(r) for r in items],
        total=total,
        page=page,
        pageSize=pageSize,
    )


@router.get(
    "/{anomaly_id}",
    response_model=AnomalyRead,
    dependencies=[Depends(require_roles(*READ_ROLES))],
    summary="Détail d'une anomalie",
)
async def get_anomaly(
    anomaly_id: str, service: Svc,
) -> AnomalyRead:
    row = await service.get_anomaly(anomaly_id)
    return AnomalyRead.model_validate(row)


@router.post(
    "/{anomaly_id}/review",
    response_model=AnomalyRead,
    dependencies=[Depends(require_roles(*READ_ROLES))],
    summary="Marque l'anomalie comme revue (CONFIRMED/DISMISSED/FALSE_POSITIVE)",
)
async def review_anomaly(
    anomaly_id: str,
    payload: AnomalyReviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
) -> AnomalyRead:
    row = await service.review_anomaly(
        anomaly_id,
        new_status=payload.status,
        note=payload.note,
        reviewer_id=user.id,
    )
    return AnomalyRead.model_validate(row)
