"""Module 15 — Routeur admin / settings plateforme.

Endpoints
---------
* ``GET    /api/admin/settings``                    — liste tout (RBAC NATIONAL/MINISTRY)
* ``PUT    /api/admin/settings/{key}``              — upsert typé          (idem)
* ``GET    /api/admin/feature-flags``               — liste flags         (idem)
* ``PUT    /api/admin/feature-flags/{key}``         — upsert flag         (idem)
* ``POST   /api/admin/maintenance/enable``          — bascule lecture seule (idem)
* ``POST   /api/admin/maintenance/disable``         — désactive            (idem)
* ``GET    /api/admin/maintenance``                 — lecture statut       (idem)
* ``GET    /api/admin/changes``                     — audit history        (idem)
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.exceptions import NotFoundError
from app.modules.admin.models import (
    FeatureFlag,
    PlatformSetting,
    SettingChangeLog,
)
from app.modules.admin.service import AdminService
from app.modules.auth.models import User
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

ADMIN_ROLES = (UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN)

CurrentUserDep = Annotated[User, Depends(get_current_user)]
AdminGuard = Annotated[User, Depends(require_roles(*ADMIN_ROLES))]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class SettingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    key: str
    type: str
    value: Any = Field(alias="valueJson")
    description: str | None = None
    updatedById: str | None = None
    updatedAt: datetime


class UpsertSettingRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    value: Any
    type: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=2000)


class FeatureFlagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    key: str
    enabled: bool
    rolloutPercentage: int
    description: str | None = None
    updatedAt: datetime


class UpsertFeatureFlagRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    enabled: bool
    rolloutPercentage: int = Field(default=0, ge=0, le=100)
    description: str | None = Field(default=None, max_length=2000)


class MaintenanceStatus(BaseModel):
    enabled: bool


class ChangeLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    key: str
    kind: str
    oldValue: Any | None = None
    newValue: Any | None = None
    changedById: str | None = None
    changedAt: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _setting_to_read(s: PlatformSetting) -> SettingRead:
    return SettingRead.model_validate(s)


def _flag_to_read(f: FeatureFlag) -> FeatureFlagRead:
    return FeatureFlagRead.model_validate(f)


def _change_to_read(c: SettingChangeLog) -> ChangeLogRead:
    return ChangeLogRead.model_validate(c)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
router = APIRouter(tags=["admin"])


@router.get(
    "/settings",
    response_model=list[SettingRead],
    summary="Liste des paramètres plateforme",
)
async def list_settings(user: AdminGuard, session: DbSession) -> list[SettingRead]:
    _ = user
    svc = AdminService(session)
    rows = await svc.list_settings()
    return [_setting_to_read(s) for s in rows]


@router.put(
    "/settings/{key}",
    response_model=SettingRead,
    summary="Crée ou met à jour un paramètre typé",
)
async def upsert_setting(
    key: str,
    body: UpsertSettingRequest,
    user: AdminGuard,
    session: DbSession,
) -> SettingRead:
    svc = AdminService(session)
    setting = await svc.set_setting(
        key,
        body.value,
        type_=body.type,
        description=body.description,
        actor_id=user.id,
    )
    return _setting_to_read(setting)


@router.get(
    "/feature-flags",
    response_model=list[FeatureFlagRead],
    summary="Liste des feature flags",
)
async def list_feature_flags(user: AdminGuard, session: DbSession) -> list[FeatureFlagRead]:
    _ = user
    svc = AdminService(session)
    rows = await svc.list_feature_flags()
    return [_flag_to_read(f) for f in rows]


@router.put(
    "/feature-flags/{key}",
    response_model=FeatureFlagRead,
    summary="Crée ou met à jour un feature flag",
)
async def upsert_feature_flag(
    key: str,
    body: UpsertFeatureFlagRequest,
    user: AdminGuard,
    session: DbSession,
) -> FeatureFlagRead:
    svc = AdminService(session)
    flag = await svc.set_feature_flag(
        key,
        enabled=body.enabled,
        rollout_percentage=body.rolloutPercentage,
        description=body.description,
        actor_id=user.id,
    )
    return _flag_to_read(flag)


@router.post(
    "/maintenance/enable",
    response_model=MaintenanceStatus,
    summary="Active le mode maintenance (lecture seule globale)",
)
async def enable_maintenance(
    user: AdminGuard, session: DbSession,
) -> MaintenanceStatus:
    svc = AdminService(session)
    await svc.enable_maintenance_mode(actor_id=user.id)
    return MaintenanceStatus(enabled=True)


@router.post(
    "/maintenance/disable",
    response_model=MaintenanceStatus,
    summary="Désactive le mode maintenance",
)
async def disable_maintenance(
    user: AdminGuard, session: DbSession,
) -> MaintenanceStatus:
    svc = AdminService(session)
    await svc.disable_maintenance_mode(actor_id=user.id)
    return MaintenanceStatus(enabled=False)


@router.get(
    "/maintenance",
    response_model=MaintenanceStatus,
    summary="Statut courant du mode maintenance",
)
async def maintenance_status(
    user: AdminGuard, session: DbSession,
) -> MaintenanceStatus:
    _ = user
    svc = AdminService(session)
    return MaintenanceStatus(enabled=await svc.is_maintenance_mode())


@router.get(
    "/changes",
    response_model=list[ChangeLogRead],
    summary="Historique d'audit des changements (paginé)",
)
async def list_changes(
    user: AdminGuard,
    session: DbSession,
    key: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ChangeLogRead]:
    _ = user
    svc = AdminService(session)
    rows = await svc.list_changes(key=key, limit=limit)
    return [_change_to_read(c) for c in rows]


__all__ = ["NotFoundError", "router"]
