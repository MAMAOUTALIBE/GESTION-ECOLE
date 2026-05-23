from typing import Annotated

from fastapi import APIRouter, Depends

from app.modules.attendance.schemas import (
    AttendanceRecordRead,
    ScanAttendanceRequest,
    ScanAttendanceResponse,
)
from app.modules.attendance.service import AttendanceService
from app.modules.auth.models import User
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

ATTENDANCE_SCAN_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
    UserRole.TEACHER,
    UserRole.CENSUS_AGENT,
)


def _service(session: DbSession) -> AttendanceService:
    return AttendanceService(session)


AttSvc = Annotated[AttendanceService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["attendance"])


@router.get(
    "/today",
    response_model=list[AttendanceRecordRead],
    summary="Scans QR du jour (filtrés par scope territorial)",
)
async def attendance_today(
    user: CurrentUserDep, service: AttSvc
) -> list[AttendanceRecordRead]:
    return await service.today(user)


@router.post(
    "/scan",
    response_model=ScanAttendanceResponse,
    dependencies=[Depends(require_roles(*ATTENDANCE_SCAN_ROLES))],
    summary="Enregistrer un scan QR (déduplication sur la journée)",
)
async def attendance_scan(
    dto: ScanAttendanceRequest, user: CurrentUserDep, service: AttSvc
) -> ScanAttendanceResponse:
    return await service.scan(user, dto)
