"""Module 1A — Router HTTP du module Enrollment.

Endpoints
---------
* ``POST   /api/enrollment``                 — saisie unitaire.
* ``POST   /api/enrollment/bulk``            — saisie groupée (max 200).
* ``GET    /api/enrollment/school/{id}``     — liste pour une école.
* ``GET    /api/enrollment/aggregate``       — agrégats par scope + filtres.
* ``POST   /api/enrollment/compute-from-students`` — recalcul depuis Student
  (admin central uniquement).

RBAC
----
* Lecture (list_for_school, aggregate) : tous les rôles authentifiés
  (le scope territorial filtre automatiquement les rows accessibles).
* Écriture (record, bulk_record) : admins territoriaux + CENSUS_AGENT +
  SCHOOL_DIRECTOR. TEACHER et INSPECTOR sont exclus (pas leur métier).
* compute_from_students : NATIONAL_ADMIN + MINISTRY_ADMIN.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.enrollment.enums import EnrollmentClassLevel, EnrollmentSource
from app.modules.enrollment.schemas import (
    AggregateRequest,
    AggregateResponse,
    AggregateScope,
    BulkRecordResponse,
    EnrollmentBulkCreate,
    EnrollmentCreate,
    EnrollmentRead,
)
from app.modules.enrollment.service import EnrollmentService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import Gender, UserRole
from app.shared.permissions import require_roles

# Roles autorisés à écrire un effectif (déclarer un recensement). On
# exclut TEACHER (n'a pas la responsabilité de saisir le census) et
# INSPECTOR (lecteur, pas saisisseur).
ENROLLMENT_WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
    UserRole.CENSUS_AGENT,
)

COMPUTE_FROM_STUDENTS_HTTP_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
)


def _service(session: DbSession) -> EnrollmentService:
    return EnrollmentService(session)


EnrollmentSvc = Annotated[EnrollmentService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["enrollment"])


@router.post(
    "",
    response_model=EnrollmentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*ENROLLMENT_WRITE_ROLES))],
    summary="Enregistre un effectif désagrégé (niveau × genre)",
)
async def record(
    dto: EnrollmentCreate, user: CurrentUserDep, service: EnrollmentSvc
) -> EnrollmentRead:
    return await service.record(dto, user)


@router.post(
    "/bulk",
    response_model=BulkRecordResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*ENROLLMENT_WRITE_ROLES))],
    summary="Saisie groupée — max 200 lignes par appel",
)
async def bulk_record(
    payload: EnrollmentBulkCreate,
    user: CurrentUserDep,
    service: EnrollmentSvc,
) -> BulkRecordResponse:
    return await service.bulk_record(payload.items, user)


@router.get(
    "/school/{school_id}",
    response_model=list[EnrollmentRead],
    summary="Liste les effectifs d'une école (filtrable par année)",
)
async def list_for_school(
    school_id: str,
    user: CurrentUserDep,
    service: EnrollmentSvc,
    schoolYearId: Annotated[str | None, Query(max_length=30)] = None,
) -> list[EnrollmentRead]:
    return await service.list_for_school(
        school_id, user, school_year_id=schoolYearId
    )


@router.get(
    "/aggregate",
    response_model=AggregateResponse,
    summary="Agrégats par niveau, genre et breakdown",
)
async def aggregate(
    user: CurrentUserDep,
    service: EnrollmentSvc,
    schoolYearId: Annotated[str, Query(max_length=30)],
    scope: AggregateScope = AggregateScope.NATIONAL,
    regionId: Annotated[str | None, Query(max_length=30)] = None,
    prefectureId: Annotated[str | None, Query(max_length=30)] = None,
    subPrefectureId: Annotated[str | None, Query(max_length=30)] = None,
    schoolId: Annotated[str | None, Query(max_length=30)] = None,
    classLevel: EnrollmentClassLevel | None = None,
    gender: Gender | None = None,
    source: EnrollmentSource = EnrollmentSource.CENSUS_DECLARED,
) -> AggregateResponse:
    req = AggregateRequest(
        scope=scope,
        schoolYearId=schoolYearId,
        regionId=regionId,
        prefectureId=prefectureId,
        subPrefectureId=subPrefectureId,
        schoolId=schoolId,
        classLevel=classLevel,
        gender=gender,
        source=source,
    )
    return await service.aggregate(req, user)


@router.post(
    "/compute-from-students",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*COMPUTE_FROM_STUDENTS_HTTP_ROLES))],
    summary="Recalcule les effectifs depuis la base élèves (admin central)",
)
async def compute_from_students(
    user: CurrentUserDep,
    service: EnrollmentSvc,
    schoolYearId: Annotated[str, Query(max_length=30)],
) -> dict[str, int]:
    inserted = await service.compute_from_students(schoolYearId, user)
    return {"inserted": inserted}


__all__ = ["ENROLLMENT_WRITE_ROLES", "router"]
