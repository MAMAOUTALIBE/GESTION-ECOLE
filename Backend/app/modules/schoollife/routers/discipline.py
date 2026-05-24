"""Router discipline (Module 7) — gère les incidents et sanctions.

Endpoints :
    POST   /incidents
    GET    /incidents (filtres : schoolId, severity, status)
    PATCH  /incidents/{id}
    GET    /incidents/by-student/{studentId}
    GET    /incidents/stats

RBAC :
    * Écriture (POST/PATCH) : SCHOOL_DIRECTOR + admins
    * Lecture : SCHOOL_DIRECTOR + admins (NATIONAL_ADMIN voit tout)
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.modules.auth.models import User
from app.modules.pii_audit.enums import PiiEntityType
from app.modules.pii_audit.service import PiiAuditService
from app.modules.schoollife.enums import IncidentStatus
from app.modules.schoollife.schemas import (
    CreateIncidentRequest,
    IncidentRead,
    IncidentStatsResponse,
    UpdateIncidentRequest,
)
from app.modules.schoollife.service import DiscplineService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import IncidentSeverity, UserRole
from app.shared.permissions import require_roles

router = APIRouter(tags=["schoollife-discipline"])

READ_ROLES = (
    UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN, UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN, UserRole.SCHOOL_DIRECTOR,
    UserRole.INSPECTOR,
)
WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN, UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN, UserRole.SCHOOL_DIRECTOR,
)


def _svc(session: DbSession) -> DiscplineService:
    return DiscplineService(session)


Svc = Annotated[DiscplineService, Depends(_svc)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.post(
    "/incidents",
    response_model=IncidentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def create_incident(
    dto: CreateIncidentRequest, user: CurrentUserDep, service: Svc,
) -> IncidentRead:
    return await service.create_incident(user, dto)


@router.get(
    "/incidents",
    response_model=list[IncidentRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def list_incidents(
    user: CurrentUserDep, service: Svc,
    request: Request,
    schoolId: Annotated[str | None, Query()] = None,
    severity: Annotated[IncidentSeverity | None, Query()] = None,
    incidentStatus: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[IncidentRead]:
    incidents = await service.list_incidents(
        user, schoolId, severity, incidentStatus, limit,
    )
    # Module 5C — audit PII : la liste d'incidents disciplinaires
    # touche à des mineurs ET à des sanctions — donnée très sensible.
    try:
        audit = PiiAuditService(service.session)
        await audit.log_bulk_list(
            actor=user,
            entity_type=PiiEntityType.INCIDENT,
            entity_ids=[i.id for i in incidents],
            endpoint=request.url.path,
            request=request,
        )
    except Exception:
        pass
    return incidents


@router.patch(
    "/incidents/{incident_id}",
    response_model=IncidentRead,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def update_incident(
    incident_id: str, dto: UpdateIncidentRequest,
    user: CurrentUserDep, service: Svc,
) -> IncidentRead:
    return await service.update_incident(user, incident_id, dto)


@router.get(
    "/incidents/by-student/{student_id}",
    response_model=list[IncidentRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def by_student(
    student_id: str, user: CurrentUserDep, service: Svc,
) -> list[IncidentRead]:
    return await service.list_by_student(user, student_id)


@router.get(
    "/incidents/stats",
    response_model=IncidentStatsResponse,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def stats(
    user: CurrentUserDep, service: Svc,
    schoolId: Annotated[str | None, Query()] = None,
) -> IncidentStatsResponse:
    return await service.stats(user, schoolId)
