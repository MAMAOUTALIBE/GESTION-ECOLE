from typing import Annotated

import jwt
from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.exceptions import UnauthorizedError
from app.core.observability import auth_revocation_check_failed_total
from app.core.redis import get_redis
from app.core.security import decode_token, is_token_revoked

# IMPORTANT: bearer scheme. auto_error=False so we can raise our own typed error.
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: DbSession,
    request: Request,
):  # type: ignore[no-untyped-def]
    """Resolve the authenticated user from the Authorization header.

    Returns the SQLAlchemy User instance. Raises UnauthorizedError on any
    failure (missing/invalid/expired token, unknown user, deactivated user).

    Security fix C-5 — Redis outage on the JTI revocation check no longer
    fails open. The previous behaviour let a revoked-but-not-yet-expired
    access token keep working as long as Redis was down. We now raise a
    503 (so monitoring can distinguish from a normal 401) with a clear
    message, and we both log + increment a Prometheus counter so ops
    notice immediately. Rationale: the auth surface is the project's
    "régalien" boundary — fail-closed is the only acceptable default.
    """
    # Imported here to avoid circular import at module load time
    from app.modules.auth.models import User

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError(detail="Missing bearer token")

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except jwt.ExpiredSignatureError as e:
        raise UnauthorizedError(detail="Token expired") from e
    except jwt.InvalidTokenError as e:
        raise UnauthorizedError(detail="Invalid token") from e

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError(detail="Token missing subject")

    # Module 1 — fast Redis blacklist check on the JTI. Tokens minted before
    # Module 1 may not carry a jti; we skip the check (graceful rollout).
    # Fix C-5 — fail-CLOSED when Redis is unreachable: a revoked token
    # MUST NOT keep working just because the blacklist is unavailable.
    # TODO Module 1.1: allow read-only fallback when JWT was minted < 5 min
    # ago (the operator can opt-in via a flag) so a brief Redis blip doesn't
    # kick everyone out simultaneously.
    jti = payload.get("jti")
    if jti:
        try:
            redis = get_redis()
            revoked = await is_token_revoked(redis, jti)
        except UnauthorizedError:
            raise
        except Exception as exc:
            auth_revocation_check_failed_total.inc()
            request_id = getattr(request.state, "request_id", None)
            logger.error(
                "auth: revocation check failed (request_id={}, user_id={}, jti={}): {}",
                request_id,
                user_id,
                jti,
                exc,
            )
            raise UnauthorizedError(
                detail="Service d'authentification temporairement indisponible. Réessayez.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        if revoked:
            raise UnauthorizedError(detail="Token révoqué")

    user = await session.get(User, user_id)
    if user is None:
        raise UnauthorizedError(detail="User not found")
    if not user.isActive:
        raise UnauthorizedError(detail="User is deactivated")

    return user


# Routers should annotate the parameter directly:
#   from app.modules.auth.models import User
#   user: Annotated[User, Depends(get_current_user)]
# (We don't expose a `CurrentUser` type alias here to avoid a circular
# import on `User`.)
