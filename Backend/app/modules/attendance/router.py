"""Attendance HTTP router — scan, today, bulk, stats, partitions.

Module 3 enrichit le module attendance avec :
* ``POST /api/attendance/bulk`` : ingestion en lot (≤ 200 scans / appel),
  réservée aux directeurs et au-dessus (les enseignants restent sur le
  POST /scan unitaire pour limiter le risque de saisie en masse).
* ``GET /api/attendance/stats`` : statistiques agrégées par bucket
  (day | week | month) avec cache Redis et scope territorial appliqué.
* ``GET /api/attendance/partitions`` : introspection des partitions
  (debug + monitoring), réservé NATIONAL_ADMIN / MINISTRY_ADMIN.
* ``POST /api/attendance/partitions/ensure`` : pré-création des
  partitions futures (sert au job Celery + debug manuel).
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError

from app.core.exceptions import ValidationFailedError
from app.modules.attendance.partitions import (
    ensure_future_partitions,
    list_partitions,
)
from app.modules.attendance.schemas import (
    AttendanceRecordRead,
    AttendanceStatsFilter,
    AttendanceStatsResponse,
    BulkScanRequest,
    BulkScanResult,
    EnsurePartitionsResponse,
    PartitionInfo,
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

# Bulk scan : un teacher ne fait que des scans unitaires (anti-erreur).
ATTENDANCE_BULK_SCAN_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
)

# Stats : tous les rôles consultatifs et au-dessus.
ATTENDANCE_STATS_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.INSPECTOR,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
    UserRole.TEACHER,
)

# Partitions : ops only.
ATTENDANCE_PARTITION_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
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


@router.post(
    "/bulk",
    response_model=BulkScanResult,
    dependencies=[Depends(require_roles(*ATTENDANCE_BULK_SCAN_ROLES))],
    summary="Ingestion en lot de scans (≤ 200 par appel, idempotent par jour)",
)
async def attendance_bulk_scan(
    dto: BulkScanRequest, user: CurrentUserDep, service: AttSvc
) -> BulkScanResult:
    return await service.bulk_scan(user, dto)


@router.get(
    "/stats",
    response_model=AttendanceStatsResponse,
    dependencies=[Depends(require_roles(*ATTENDANCE_STATS_ROLES))],
    summary="Statistiques de présence agrégées par bucket (day|week|month)",
)
async def attendance_stats(
    user: CurrentUserDep,
    service: AttSvc,
    schoolId: str | None = Query(default=None, max_length=30),
    classRoomId: str | None = Query(default=None, max_length=30),
    studentId: str | None = Query(default=None, max_length=30),
    dateFrom: str = Query(..., description="ISO date inclus (YYYY-MM-DD)"),
    dateTo: str = Query(..., description="ISO date inclus (YYYY-MM-DD)"),
    groupBy: str = Query(default="day", pattern="^(day|week|month)$"),
) -> AttendanceStatsResponse:
    try:
        filters = AttendanceStatsFilter(
            schoolId=schoolId,
            classRoomId=classRoomId,
            studentId=studentId,
            dateFrom=dateFrom,  # type: ignore[arg-type]
            dateTo=dateTo,  # type: ignore[arg-type]
            groupBy=groupBy,  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        # Remap en 422 propre côté FastAPI au lieu d'un 500.
        raise ValidationFailedError(
            detail="Filtres invalides", extra={"errors": exc.errors()}
        ) from exc
    return await service.attendance_stats(user, filters)


@router.get(
    "/partitions",
    response_model=list[PartitionInfo],
    dependencies=[Depends(require_roles(*ATTENDANCE_PARTITION_ROLES))],
    summary="Liste des partitions mensuelles (debug / monitoring)",
)
async def attendance_partitions(
    user: CurrentUserDep, session: DbSession
) -> list[PartitionInfo]:
    rows = await list_partitions(session)
    return [PartitionInfo.model_validate(r) for r in rows]


@router.post(
    "/partitions/ensure",
    response_model=EnsurePartitionsResponse,
    dependencies=[Depends(require_roles(*ATTENDANCE_PARTITION_ROLES))],
    summary="Pré-créer les partitions futures (cron / debug)",
)
async def attendance_partitions_ensure(
    user: CurrentUserDep,
    session: DbSession,
    months_ahead: int = Query(default=3, ge=0, le=24),
) -> EnsurePartitionsResponse:
    before = {row["name"] for row in await list_partitions(session)}
    created = await ensure_future_partitions(session, months_ahead=months_ahead)
    return EnsurePartitionsResponse(
        created=created,
        already_present=sorted(before - set(created)),
    )
