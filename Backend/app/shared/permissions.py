from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import Depends

from app.core.exceptions import ForbiddenError
from app.shared.deps import get_current_user
from app.shared.enums import UserRole

if TYPE_CHECKING:
    from app.modules.auth.models import User

# Functional groupings (mirror Angular auth.service.ts groups)
NATIONAL_SCOPE_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN}
)
REGIONAL_SCOPE_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.REGIONAL_ADMIN, UserRole.INSPECTOR}
)
PREFECTURE_SCOPE_ROLES: frozenset[UserRole] = frozenset({UserRole.PREFECTURE_ADMIN})
SUB_PREFECTURE_SCOPE_ROLES: frozenset[UserRole] = frozenset({UserRole.SUB_PREFECTURE_ADMIN})
SCHOOL_SCOPE_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.SCHOOL_DIRECTOR, UserRole.TEACHER, UserRole.CENSUS_AGENT}
)

ACADEMIC_WRITE_ROLES: frozenset[UserRole] = frozenset(
    {
        UserRole.NATIONAL_ADMIN,
        UserRole.MINISTRY_ADMIN,
        UserRole.SCHOOL_DIRECTOR,
        UserRole.TEACHER,
    }
)


def is_national_scope(role: UserRole) -> bool:
    return role in NATIONAL_SCOPE_ROLES


def is_regional_scope(role: UserRole) -> bool:
    return role in REGIONAL_SCOPE_ROLES


def is_school_scope(role: UserRole) -> bool:
    return role in SCHOOL_SCOPE_ROLES


def require_roles(*allowed: UserRole) -> Callable[..., Any]:
    """FastAPI dependency factory enforcing role-based access.

    Usage in a router:
        @router.get(
            "/students",
            dependencies=[Depends(require_roles(UserRole.SCHOOL_DIRECTOR))],
        )

    Or to also receive the user instance:
        async def handler(user: Annotated[User, Depends(require_roles(...))]):
    """
    allowed_set = frozenset(allowed)

    async def _checker(current_user: "User" = Depends(get_current_user)) -> "User":
        if current_user.role not in allowed_set:
            raise ForbiddenError(
                detail=f"Role '{current_user.role}' is not allowed for this action.",
                extra={"required_any_of": [r.value for r in allowed_set]},
            )
        return current_user

    return _checker
