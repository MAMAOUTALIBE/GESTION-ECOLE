from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.modules.auth.models import User
from app.modules.territory.schemas import (
    CreatePrefectureRequest,
    CreateSubPrefectureRequest,
    PrefectureListItem,
    PrefectureRead,
    RegionRead,
    SetZoneTypeRequest,
    SubPrefectureListItem,
    SubPrefectureRead,
    SubPrefectureZoneItem,
)
from app.modules.territory.service import (
    SET_SUBPREFECTURE_ZONE_ROLES,
    TerritoryService,
)
from app.shared.deps import DbSession, get_current_user
from app.shared.permissions import require_roles

router = APIRouter(tags=["territory"])


def _service(session: DbSession) -> TerritoryService:
    return TerritoryService(session)


TerritorySvc = Annotated[TerritoryService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.get(
    "/regions",
    response_model=list[RegionRead],
    summary="Liste des régions dans le périmètre de l'utilisateur",
)
async def list_regions(
    user: CurrentUserDep, service: TerritorySvc
) -> list[RegionRead]:
    regions = await service.list_regions(user)
    return [RegionRead.model_validate(r) for r in regions]


@router.get(
    "/prefectures",
    response_model=list[PrefectureListItem],
    summary="Liste des préfectures dans le périmètre de l'utilisateur",
)
async def list_prefectures(
    user: CurrentUserDep, service: TerritorySvc
) -> list[PrefectureListItem]:
    return await service.list_prefectures(user)


@router.post(
    "/prefectures",
    response_model=PrefectureRead,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une préfecture (validation hiérarchique si non-national)",
)
async def create_prefecture(
    dto: CreatePrefectureRequest, user: CurrentUserDep, service: TerritorySvc
) -> PrefectureRead:
    return await service.create_prefecture(user, dto)


@router.get(
    "/sub-prefectures",
    response_model=list[SubPrefectureListItem],
    summary="Liste des sous-préfectures dans le périmètre de l'utilisateur",
)
async def list_sub_prefectures(
    user: CurrentUserDep, service: TerritorySvc
) -> list[SubPrefectureListItem]:
    return await service.list_sub_prefectures(user)


@router.post(
    "/sub-prefectures",
    response_model=SubPrefectureRead,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une sous-préfecture (validation par la région si initiée par la préfecture)",
)
async def create_sub_prefecture(
    dto: CreateSubPrefectureRequest, user: CurrentUserDep, service: TerritorySvc
) -> SubPrefectureRead:
    return await service.create_sub_prefecture(user, dto)


# ---------------------------------------------------------------------------
# Module 1C — zone urbain / rural d'une sous-préfecture (référentiel INS)
# ---------------------------------------------------------------------------
@router.get(
    "/sub-prefectures/zones",
    response_model=list[SubPrefectureZoneItem],
    summary="Liste des sous-préfectures avec leur zone INS + compteur écoles",
)
async def list_sub_prefectures_with_zone(
    user: CurrentUserDep, service: TerritorySvc,
) -> list[SubPrefectureZoneItem]:
    return await service.list_sub_prefectures_with_zone(user)


@router.put(
    "/sub-prefectures/{sub_prefecture_id}/zone-type",
    response_model=SubPrefectureRead,
    dependencies=[Depends(require_roles(*SET_SUBPREFECTURE_ZONE_ROLES))],
    summary="Modifie la zone INS d'une sous-préfecture (NATIONAL/MINISTRY)",
)
async def set_sub_prefecture_zone_type(
    sub_prefecture_id: str,
    dto: SetZoneTypeRequest,
    user: CurrentUserDep,
    service: TerritorySvc,
) -> SubPrefectureRead:
    return await service.set_sub_prefecture_zone_type(
        sub_prefecture_id, dto.zoneType, user,
    )
