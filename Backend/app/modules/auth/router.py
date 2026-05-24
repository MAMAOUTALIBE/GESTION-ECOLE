from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.proxy import client_ip
from app.core.redis import get_redis
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MeResponse,
    MfaDisableRequest,
    MfaSetupRequest,
    MfaSetupResponse,
    MfaVerifyRequest,
    MfaVerifySetupRequest,
    RefreshRequest,
    ResetPasswordRequest,
    SessionInfo,
    UserListItem,
    UserUpdate,
)
from app.modules.auth.service import AuthService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

router = APIRouter(tags=["auth"])

# Module 1 — a soft bearer to grab the access token on /logout. auto_error
# is False so the endpoint stays callable when the client lost the token.
_soft_bearer = HTTPBearer(auto_error=False)


def _service(session: DbSession, redis: Annotated[Redis, Depends(get_redis)]) -> AuthService:
    return AuthService(session, redis=redis)


AuthSvc = Annotated[AuthService, Depends(_service)]
ADMIN_USER_ROLES = (UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN)


def _request_meta(request: Request) -> tuple[str | None, str | None]:
    """Return `(ip, user_agent)` — both nullable for tests.

    Security fix C-4 — `ip` now goes through :func:`app.core.proxy.client_ip`
    which honours ``TRUSTED_PROXIES`` instead of blindly trusting either
    ``request.client.host`` (broken behind reverse proxies) or the raw
    ``X-Forwarded-For`` header (spoofable when not behind one).
    """
    ip = client_ip(request)
    ua = request.headers.get("user-agent")
    return ip, ua


# ---------------------------------------------------------------------------
# Login / Me / Users — original three (kept byte-compatible)
# ---------------------------------------------------------------------------
@router.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Connexion par email + mot de passe",
)
async def login(
    dto: LoginRequest,
    request: Request,
    service: AuthSvc,
) -> LoginResponse:
    ip, ua = _request_meta(request)
    return await service.login(dto, ip_address=ip, user_agent=ua)


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Profil de l'utilisateur authentifié",
)
async def me(current_user: Annotated[User, Depends(get_current_user)]) -> MeResponse:
    return AuthService.me(current_user)


@router.patch(
    "/me",
    response_model=MeResponse,
    summary="Mettre à jour son profil (langue préférée — Module 6)",
)
async def update_me(
    dto: UserUpdate,
    session: DbSession,
    current_user: Annotated[User, Depends(get_current_user)],
) -> MeResponse:
    if dto.preferredLanguage is not None:
        current_user.preferredLanguage = dto.preferredLanguage
        await session.flush()
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


# ---------------------------------------------------------------------------
# Module 1 — MFA verify (after login challenge)
# ---------------------------------------------------------------------------
@router.post(
    "/mfa/verify",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Vérifier le code MFA et obtenir les tokens",
)
async def mfa_verify(
    dto: MfaVerifyRequest,
    request: Request,
    service: AuthSvc,
) -> LoginResponse:
    ip, ua = _request_meta(request)
    return await service.verify_mfa(
        dto.challengeToken, dto.code, ip_address=ip, user_agent=ua
    )


# ---------------------------------------------------------------------------
# Module 1 — Refresh / Logout
# ---------------------------------------------------------------------------
@router.post(
    "/refresh",
    response_model=LoginResponse,
    summary="Renouveler le couple access+refresh (rotation)",
)
async def refresh(
    dto: RefreshRequest,
    request: Request,
    service: AuthSvc,
) -> LoginResponse:
    ip, ua = _request_meta(request)
    return await service.refresh(dto.refreshToken, ip_address=ip, user_agent=ua)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Révoquer le refresh token + blacklister l'access token",
)
async def logout(
    dto: LogoutRequest,
    request: Request,
    service: AuthSvc,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_soft_bearer)
    ],
) -> None:
    ip, ua = _request_meta(request)
    access_token = credentials.credentials if credentials else None
    user = await _try_load_user_from_token(service, access_token)
    await service.logout(
        access_token=access_token,
        refresh_token=dto.refreshToken,
        user=user,
        ip_address=ip,
        user_agent=ua,
    )


# ---------------------------------------------------------------------------
# Module 1 — Password change / forgot / reset
# ---------------------------------------------------------------------------
@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Changer son propre mot de passe",
)
async def change_password(
    dto: ChangePasswordRequest,
    request: Request,
    service: AuthSvc,
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    ip, ua = _request_meta(request)
    await service.change_password(
        current_user,
        dto.currentPassword,
        dto.newPassword,
        dto.confirmPassword,
        ip_address=ip,
        user_agent=ua,
    )


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Demander un email de réinitialisation",
)
async def forgot_password(
    dto: ForgotPasswordRequest,
    request: Request,
    service: AuthSvc,
) -> dict[str, str]:
    """Always 202 + the same body — never leaks email existence."""
    ip, ua = _request_meta(request)
    await service.forgot_password(dto.email, ip_address=ip, user_agent=ua)
    return {"message": "Si l'email existe, un lien a été envoyé."}


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Réinitialiser le mot de passe avec le token reçu par email",
)
async def reset_password(
    dto: ResetPasswordRequest,
    request: Request,
    service: AuthSvc,
) -> None:
    ip, ua = _request_meta(request)
    await service.reset_password(
        dto.token, dto.newPassword, dto.confirmPassword,
        ip_address=ip, user_agent=ua,
    )


# ---------------------------------------------------------------------------
# Module 1 — MFA setup / verify-setup / disable
# ---------------------------------------------------------------------------
@router.post(
    "/mfa/setup",
    response_model=MfaSetupResponse,
    summary="Démarrer la configuration MFA (secret + QR + recovery codes)",
)
async def mfa_setup(
    dto: MfaSetupRequest,
    request: Request,
    service: AuthSvc,
    current_user: Annotated[User, Depends(get_current_user)],
) -> MfaSetupResponse:
    """Fix C-1 — body is now mandatory; see :class:`MfaSetupRequest`."""
    ip, ua = _request_meta(request)
    return await service.setup_mfa(
        current_user,
        current_password=dto.currentPassword,
        current_totp=dto.currentTotp,
        ip_address=ip,
        user_agent=ua,
    )


@router.post(
    "/mfa/verify-setup",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Activer la MFA après scan du QR",
)
async def mfa_verify_setup(
    dto: MfaVerifySetupRequest,
    request: Request,
    service: AuthSvc,
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    ip, ua = _request_meta(request)
    await service.verify_mfa_setup(
        current_user, dto.code, ip_address=ip, user_agent=ua
    )


@router.post(
    "/mfa/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Désactiver la MFA (double vérification : mot de passe + code)",
)
async def mfa_disable(
    dto: MfaDisableRequest,
    request: Request,
    service: AuthSvc,
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    ip, ua = _request_meta(request)
    await service.disable_mfa(
        current_user, dto.password, dto.code, ip_address=ip, user_agent=ua
    )


# ---------------------------------------------------------------------------
# Module 1 — Sessions (list / revoke)
# ---------------------------------------------------------------------------
@router.get(
    "/sessions",
    response_model=list[SessionInfo],
    summary="Lister mes sessions actives",
)
async def list_sessions(
    service: AuthSvc,
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[SessionInfo]:
    return await service.list_sessions(current_user)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Révoquer une session active",
)
async def revoke_session(
    session_id: str,
    request: Request,
    service: AuthSvc,
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    ip, ua = _request_meta(request)
    await service.revoke_session(
        current_user, session_id, ip_address=ip, user_agent=ua
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _try_load_user_from_token(
    service: AuthService, access_token: str | None
) -> User | None:
    """Best-effort lookup of the current user from an access token — used by
    logout so we can include the userId in the audit log even when the token
    has already been blacklisted by another tab.
    """
    if not access_token:
        return None
    try:
        from app.core.security import decode_token  # local import: avoid cycles

        payload = decode_token(access_token, expected_type="access")
    except Exception:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    return await service.session.get(User, sub)
