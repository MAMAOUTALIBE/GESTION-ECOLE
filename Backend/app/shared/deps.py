from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.exceptions import UnauthorizedError
from app.core.redis import get_redis
from app.core.security import decode_token, is_token_revoked

# IMPORTANT: bearer scheme. auto_error=False so we can raise our own typed error.
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: DbSession,
):  # type: ignore[no-untyped-def]
    """Resolve the authenticated user from the Authorization header.

    Returns the SQLAlchemy User instance. Raises UnauthorizedError on any
    failure (missing/invalid/expired token, unknown user, deactivated user).
    """
    # Imported here to avoid circular import at module load time
    from app.modules.auth.models import User  # noqa: PLC0415

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
    # Module 1 may not carry a jti; we skip the check in that case (graceful
    # rollout). Redis outage falls back to "allow" (logged inside the helper).
    jti = payload.get("jti")
    if jti:
        try:
            redis = get_redis()
            if await is_token_revoked(redis, jti):
                raise UnauthorizedError(detail="Token révoqué")
        except UnauthorizedError:
            raise
        except Exception:  # pragma: no cover - defensive (Redis down)
            pass

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
