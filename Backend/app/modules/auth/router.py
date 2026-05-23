from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.modules.auth.models import User
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    UserListItem,
)
from app.modules.auth.service import AuthService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

router = APIRouter(tags=["auth"])


def _service(session: DbSession) -> AuthService:
    return AuthService(session)


AuthSvc = Annotated[AuthService, Depends(_service)]
ADMIN_USER_ROLES = (UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN)


@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connexion par email + mot de passe",
)
async def login(dto: LoginRequest, service: AuthSvc) -> LoginResponse:
    return await service.login(dto)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Profil de l'utilisateur authentifié",
)
async def me(current_user: Annotated[User, Depends(get_current_user)]) -> MeResponse:
    return AuthService.me(current_user)


@router.get(
    "/users",
    response_model=list[UserListItem],
    dependencies=[Depends(require_roles(*ADMIN_USER_ROLES))],
    summary="Lister tous les utilisateurs (admin national/ministère)",
)
async def list_users(
    session: DbSession,
    role: Annotated[UserRole | None, Query()] = None,
    isActive: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[UserListItem]:
    """Annuaire des comptes plateforme — utilisé par /school-census/users-roles."""
    stmt = (
        select(User)
        .options(
            selectinload(User.region),
            selectinload(User.prefecture),
            selectinload(User.subPrefecture),
            selectinload(User.school),
        )
        .order_by(User.fullName.asc())
        .limit(limit)
    )
    if role is not None:
        stmt = stmt.where(User.role == role)
    if isActive is not None:
        stmt = stmt.where(User.isActive == isActive)
    rows = (await session.execute(stmt)).scalars().unique().all()
    return [
        UserListItem.model_validate({
            **{c.name: getattr(u, c.name) for c in u.__table__.columns},
            "region": u.region,
            "prefecture": u.prefecture,
            "subPrefecture": u.subPrefecture,
            "school": u.school,
            "createdAt": u.createdAt.isoformat(),
            "updatedAt": u.updatedAt.isoformat(),
        })
        for u in rows
    ]
