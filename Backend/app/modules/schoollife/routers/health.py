"""Router santé (Module 7) — visites + vaccinations + allergies.

Endpoints :
    POST   /visits                 (créer visite)
    GET    /visits                 (lister)
    POST   /vaccinations
    GET    /vaccinations           (filtres : studentId, vaccine)
    POST   /allergies
    GET    /allergies/by-student/{id}

RBAC :
    * Écriture : SCHOOL_DIRECTOR + admins
    * Lecture : SCHOOL_DIRECTOR + INSPECTOR + admins
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.modules.auth.models import User
from app.modules.pii_audit.enums import PiiEntityType
from app.modules.pii_audit.service import PiiAuditService
from app.modules.schoollife.schemas import (
    AllergyRead,
    CreateAllergyRequest,
    CreateHealthVisitRequest,
    CreateVaccinationRequest,
    HealthVisitRead,
    VaccinationRead,
)
from app.modules.schoollife.service import HealthService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

router = APIRouter(tags=["schoollife-health"])

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


def _svc(session: DbSession) -> HealthService:
    return HealthService(session)


Svc = Annotated[HealthService, Depends(_svc)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


# ----- Visits -----
@router.post(
    "/visits", response_model=HealthVisitRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def create_visit(
    dto: CreateHealthVisitRequest, user: CurrentUserDep, service: Svc,
) -> HealthVisitRead:
    return await service.create_visit(user, dto)


@router.get(
    "/visits", response_model=list[HealthVisitRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def list_visits(
    user: CurrentUserDep, service: Svc,
    request: Request,
    schoolId: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[HealthVisitRead]:
    visits = await service.list_visits(user, schoolId, limit)
    # Module 5C — audit PII : consulter des visites médicales d'enfants
    # est sensible. On consigne ids (≤50) ou agrégat (>50).
    try:
        audit = PiiAuditService(service.session)
        await audit.log_bulk_list(
            actor=user,
            entity_type=PiiEntityType.HEALTH_VISIT,
            entity_ids=[v.id for v in visits],
            endpoint=request.url.path,
            request=request,
        )
    except Exception:
        pass
    return visits


# ----- Vaccinations -----
@router.post(
    "/vaccinations", response_model=VaccinationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def create_vaccination(
    dto: CreateVaccinationRequest, user: CurrentUserDep, service: Svc,
) -> VaccinationRead:
    return await service.create_vaccination(user, dto)


@router.get(
    "/vaccinations", response_model=list[VaccinationRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def list_vaccinations(
    user: CurrentUserDep, service: Svc,
    studentId: Annotated[str | None, Query()] = None,
    vaccine: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[VaccinationRead]:
    return await service.list_vaccinations(user, studentId, vaccine, limit)


# ----- Allergies -----
@router.post(
    "/allergies", response_model=AllergyRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*WRITE_ROLES))],
)
async def create_allergy(
    dto: CreateAllergyRequest, user: CurrentUserDep, service: Svc,
) -> AllergyRead:
    return await service.create_allergy(user, dto)


@router.get(
    "/allergies/by-student/{student_id}",
    response_model=list[AllergyRead],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
async def allergies_by_student(
    student_id: str, user: CurrentUserDep, service: Svc,
) -> list[AllergyRead]:
    return await service.list_allergies_by_student(user, student_id)
