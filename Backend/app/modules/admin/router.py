"""Endpoints admin/configuration plateforme."""
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.modules.admin.models import PlatformSetting
from app.modules.auth.models import User
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

ADMIN_ROLES = (UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN)


class SettingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    value: Any  # déserialisé du JSON
    category: str
    label: str
    description: str | None = None
    valueType: str
    updatedById: str | None = None


class UpdateSettingRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    value: Any
    label: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


router = APIRouter(tags=["admin"])

CurrentUserDep = Annotated[User, Depends(get_current_user)]


def _to_read(s: PlatformSetting) -> SettingRead:
    """Désérialise le `value` JSON-encodé."""
    try:
        decoded = json.loads(s.value)
    except (ValueError, TypeError):
        decoded = s.value
    return SettingRead(
        id=s.id,
        key=s.key,
        value=decoded,
        category=s.category,
        label=s.label,
        description=s.description,
        valueType=s.valueType,
        updatedById=s.updatedById,
    )


@router.get(
    "/settings",
    response_model=list[SettingRead],
    summary="Liste des paramètres plateforme",
)
async def list_settings(user: CurrentUserDep, session: DbSession) -> list[SettingRead]:
    _ = user
    rows = (await session.execute(
        select(PlatformSetting).order_by(
            PlatformSetting.category.asc(), PlatformSetting.label.asc(),
        )
    )).scalars().all()
    return [_to_read(s) for s in rows]


@router.patch(
    "/settings/{key}",
    response_model=SettingRead,
    dependencies=[Depends(require_roles(*ADMIN_ROLES))],
    summary="Mettre à jour la valeur d'un paramètre (admin national/ministère)",
)
async def update_setting(
    key: str, dto: UpdateSettingRequest, user: CurrentUserDep, session: DbSession,
) -> SettingRead:
    setting = (await session.execute(
        select(PlatformSetting).where(PlatformSetting.key == key)
    )).scalar_one_or_none()
    if setting is None:
        raise HTTPException(status_code=404, detail="Paramètre introuvable")
    setting.value = json.dumps(dto.value)
    if dto.label is not None:
        setting.label = dto.label
    if dto.description is not None:
        setting.description = dto.description
    setting.updatedById = user.id
    await session.commit()
    await session.refresh(setting)
    return _to_read(setting)
