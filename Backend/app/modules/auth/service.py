from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import UnauthorizedError
from app.core.observability import auth_login_total
from app.core.security import (
    create_access_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponse,
    LoginUser,
    MeResponse,
    MeUser,
)

INVALID_CREDENTIALS_MESSAGE = "Identifiants invalides"


class AuthService:
    """Stateless service — instances are created per request via Depends."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def login(self, dto: LoginRequest) -> LoginResponse:
        """Authenticate via email/password and return a JWT + user payload.

        Errors are intentionally generic (matching NestJS) to avoid leaking
        whether an email exists.
        """
        normalized_email = dto.email.lower().strip()

        stmt = (
            select(User)
            .where(User.email == normalized_email)
            .options(
                selectinload(User.region),
                selectinload(User.prefecture),
                selectinload(User.subPrefecture),
                selectinload(User.school),
            )
        )
        user = (await self.session.execute(stmt)).scalar_one_or_none()

        if user is None:
            auth_login_total.labels(result="invalid").inc()
            raise UnauthorizedError(detail=INVALID_CREDENTIALS_MESSAGE)
        if not user.isActive:
            auth_login_total.labels(result="inactive").inc()
            raise UnauthorizedError(detail=INVALID_CREDENTIALS_MESSAGE)

        if not verify_password(dto.password, user.passwordHash):
            auth_login_total.labels(result="invalid").inc()
            raise UnauthorizedError(detail=INVALID_CREDENTIALS_MESSAGE)

        auth_login_total.labels(result="success").inc()

        # Migrate legacy bcrypt hashes (from the NestJS era) to Argon2 on
        # successful login. Transparent — happens at most once per user.
        if needs_rehash(user.passwordHash):
            user.passwordHash = hash_password(dto.password)
            await self.session.flush()

        access_token = create_access_token(
            user.id,
            claims={
                "role": user.role.value,
                "regionId": user.regionId,
                "prefectureId": user.prefectureId,
                "subPrefectureId": user.subPrefectureId,
                "schoolId": user.schoolId,
            },
        )

        return LoginResponse(
            accessToken=access_token,
            user=LoginUser.model_validate(user),
        )

    @staticmethod
    def me(user: User) -> MeResponse:
        """Return the authenticated user's profile (no nested objects)."""
        return MeResponse(user=MeUser.model_validate(user))
