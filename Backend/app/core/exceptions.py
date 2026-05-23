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
    default_status = status.HTTP_422_UNPROCESSABLE_ENTITY


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.detail,
            "extra": exc.extra,
        },
    )
