from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.modules.auth.models import User
from app.modules.schools.schemas import (
    ClassRoomRead,
    CreateClassRoomRequest,
    CreateSchoolRequest,
    DeletedResponse,
    SchoolRead,
    SetSchoolZoneTypeRequest,
    UpdateClassRoomRequest,
    UpdateSchoolRequest,
)
from app.modules.schools.service import (
    SET_SCHOOL_ZONE_OVERRIDE_ROLES,
    SchoolsService,
)
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

# Reproduce the NestJS SCHOOL_MANAGEMENT_ROLES + CLASS_MANAGEMENT_ROLES groups
SCHOOL_MANAGEMENT_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
)
CLASS_MANAGEMENT_ROLES = (*SCHOOL_MANAGEMENT_ROLES, UserRole.SCHOOL_DIRECTOR)


def _service(session: DbSession) -> SchoolsService:
    return SchoolsService(session)


SchoolsSvc = Annotated[SchoolsService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

# /api/schools router
schools_router = APIRouter(tags=["schools"])


@schools_router.get("", response_model=list[SchoolRead])
async def list_schools(user: CurrentUserDep, service: SchoolsSvc) -> list[SchoolRead]:
    return await service.list_schools(user)


@schools_router.get("/{school_id}", response_model=SchoolRead)
async def get_school(
    school_id: str, user: CurrentUserDep, service: SchoolsSvc
) -> SchoolRead:
    return await service.get_school(user, school_id)


@schools_router.post(
    "",
    response_model=SchoolRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*SCHOOL_MANAGEMENT_ROLES))],
)
async def create_school(
    dto: CreateSchoolRequest, user: CurrentUserDep, service: SchoolsSvc
) -> SchoolRead:
    return await service.create_school(user, dto)


@schools_router.patch(
    "/{school_id}",
    response_model=SchoolRead,
    dependencies=[Depends(require_roles(*SCHOOL_MANAGEMENT_ROLES))],
)
async def update_school(
    school_id: str,
    dto: UpdateSchoolRequest,
    user: CurrentUserDep,
    service: SchoolsSvc,
) -> SchoolRead:
    return await service.update_school(user, school_id, dto)


@schools_router.delete(
    "/{school_id}",
    response_model=DeletedResponse,
    dependencies=[Depends(require_roles(*SCHOOL_MANAGEMENT_ROLES))],
)
async def delete_school(
    school_id: str, user: CurrentUserDep, service: SchoolsSvc
) -> DeletedResponse:
    result = await service.delete_school(user, school_id)
    return DeletedResponse(**result)


# ---------------------------------------------------------------------------
# Module 1C — override zone urbain / rural pour une école
# ---------------------------------------------------------------------------
@schools_router.put(
    "/{school_id}/zone-type",
    response_model=SchoolRead,
    dependencies=[Depends(require_roles(*SET_SCHOOL_ZONE_OVERRIDE_ROLES))],
    summary="Pose / retire l'override zone urbain/rural d'une école",
)
async def set_school_zone_type(
    school_id: str,
    dto: SetSchoolZoneTypeRequest,
    user: CurrentUserDep,
    service: SchoolsSvc,
) -> SchoolRead:
    return await service.set_school_zone_type_override(
        school_id, dto.zoneType, user,
    )


# /api/classes router
classes_router = APIRouter(tags=["classes"])


@classes_router.get("", response_model=list[ClassRoomRead])
async def list_classes(
    user: CurrentUserDep, service: SchoolsSvc
) -> list[ClassRoomRead]:
    return await service.list_classes(user)


@classes_router.get("/{class_id}", response_model=ClassRoomRead)
async def get_class(
    class_id: str, user: CurrentUserDep, service: SchoolsSvc
) -> ClassRoomRead:
    return await service.get_class(user, class_id)


@classes_router.post(
    "",
    response_model=ClassRoomRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CLASS_MANAGEMENT_ROLES))],
)
async def create_class(
    dto: CreateClassRoomRequest, user: CurrentUserDep, service: SchoolsSvc
) -> ClassRoomRead:
    return await service.create_class(user, dto)


@classes_router.patch(
    "/{class_id}",
    response_model=ClassRoomRead,
    dependencies=[Depends(require_roles(*CLASS_MANAGEMENT_ROLES))],
)
async def update_class(
    class_id: str,
    dto: UpdateClassRoomRequest,
    user: CurrentUserDep,
    service: SchoolsSvc,
) -> ClassRoomRead:
    return await service.update_class(user, class_id, dto)


@classes_router.delete(
    "/{class_id}",
    response_model=DeletedResponse,
    dependencies=[Depends(require_roles(*CLASS_MANAGEMENT_ROLES))],
)
async def delete_class(
    class_id: str, user: CurrentUserDep, service: SchoolsSvc
) -> DeletedResponse:
    result = await service.delete_class(user, class_id)
    return DeletedResponse(**result)
