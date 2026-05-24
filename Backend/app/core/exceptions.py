from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse


class AppError(HTTPException):
    """Base business exception. Subclass per domain error."""

    code: str = "app_error"
    default_status: int = status.HTTP_400_BAD_REQUEST

    def __init__(
        self,
        detail: str | None = None,
        *,
        status_code: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            status_code=status_code or self.default_status,
            detail=detail or self.__class__.__name__,
        )
        self.extra = extra or {}


class NotFoundError(AppError):
    code = "not_found"
    default_status = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    code = "conflict"
    default_status = status.HTTP_409_CONFLICT


class UnauthorizedError(AppError):
    code = "unauthorized"
    default_status = status.HTTP_401_UNAUTHORIZED


class ForbiddenError(AppError):
    code = "forbidden"
    default_status = status.HTTP_403_FORBIDDEN


class ValidationFailedError(AppError):
    code = "validation_failed"
    default_status = status.HTTP_422_UNPROCESSABLE_CONTENT


class RateLimitedError(AppError):
    code = "rate_limited"
    default_status = status.HTTP_429_TOO_MANY_REQUESTS


class RevokedTokenError(AppError):
    """Raised when an access/refresh token JTI is found in the Redis blacklist."""
    code = "token_revoked"
    default_status = status.HTTP_401_UNAUTHORIZED


class PostgisUnavailableError(AppError):
    """Raised when a PostGIS-only feature is requested but the extension is
    not installed on the running Postgres server.

    Mapped to HTTP 503 because it's an environmental capability gap (not a
    client mistake). The detail message guides the operator toward
    ``CREATE EXTENSION postgis``.
    """

    code = "postgis_unavailable"
    default_status = status.HTTP_503_SERVICE_UNAVAILABLE


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.detail,
            "extra": exc.extra,
        },
    )
