"""Pydantic schemas for the attendance module — mirror NestJS mapRecord().

Module 3 ajoute :
* ``BulkScanItem`` / ``BulkScanRequest`` / ``BulkScanResult`` pour l'API bulk.
* ``AttendanceStatsFilter`` / ``AttendanceStatsPoint`` /
  ``AttendanceStatsResponse`` pour les statistiques agrégées par bucket.
* ``PartitionInfo`` pour l'endpoint d'introspection des partitions.
"""
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.schools.schemas import SchoolEmbedded
from app.shared.enums import AttendanceStatus, PersonType


class ScanAttendanceRequest(BaseModel):
    """POST /api/attendance/scan body."""
    model_config = ConfigDict(str_strip_whitespace=True)

    qrToken: str = Field(min_length=1)
    status: AttendanceStatus | None = None


class AttendanceClassRoomBrief(BaseModel):
    """Compact ClassRoom shape used inside person payload (mapRecord)."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    level: str | None = None
    schoolYear: str | None = None
    schoolId: str


class AttendancePerson(BaseModel):
    """The 'person' object embedded in mapRecord output — minimal shape."""
    id: str
    uniqueCode: str
    firstName: str
    lastName: str
    fullName: str
    school: SchoolEmbedded | None = None
    classRoom: AttendanceClassRoomBrief | None = None


class AttendanceRecordRead(BaseModel):
    """mirror NestJS mapRecord()."""
    id: str
    personType: PersonType
    status: AttendanceStatus
    scannedAt: datetime
    person: AttendancePerson | None = None


class ScanAttendanceResponse(BaseModel):
    """POST /api/attendance/scan response."""
    duplicate: bool
    record: AttendanceRecordRead


# ---------------------------------------------------------------------------
# Module 3 — bulk scan
# ---------------------------------------------------------------------------
class BulkScanItem(BaseModel):
    """Un scan unitaire dans la requête bulk.

    Exactement un de ``studentId`` ou ``teacherId`` doit être fourni.
    """
    model_config = ConfigDict(str_strip_whitespace=True)

    studentId: str | None = Field(default=None, max_length=30)
    teacherId: str | None = Field(default=None, max_length=30)
    status: AttendanceStatus = AttendanceStatus.PRESENT
    scannedAt: datetime

    @model_validator(mode="after")
    def _exactly_one_person(self) -> "BulkScanItem":
        if bool(self.studentId) == bool(self.teacherId):
            raise ValueError(
                "Exactement un de studentId/teacherId doit être fourni"
            )
        return self

    @field_validator("scannedAt")
    @classmethod
    def _ensure_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class BulkScanRequest(BaseModel):
    """POST /api/attendance/bulk body — jusqu'à 200 scans par appel."""
    items: list[BulkScanItem] = Field(min_length=1, max_length=200)


class BulkScanError(BaseModel):
    """Une erreur sur un item donné (par index dans la requête)."""
    index: int
    reason: str


class BulkScanResult(BaseModel):
    """Résultat d'un POST /api/attendance/bulk."""
    inserted: int
    skipped: int
    errors: list[BulkScanError] = Field(default_factory=list)
    by_status: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Module 3 — statistiques agrégées
# ---------------------------------------------------------------------------
GroupByKind = Literal["day", "week", "month"]


class AttendanceStatsFilter(BaseModel):
    """Filtres pour ``GET /api/attendance/stats``.

    Au moins un de ``schoolId``/``classRoomId``/``studentId`` est requis
    (sinon les admins nationaux peuvent agréger la base entière → coûteux).
    Période max : 1 an (366 jours).
    """
    model_config = ConfigDict(str_strip_whitespace=True)

    schoolId: str | None = Field(default=None, max_length=30)
    classRoomId: str | None = Field(default=None, max_length=30)
    studentId: str | None = Field(default=None, max_length=30)
    dateFrom: date
    dateTo: date
    groupBy: GroupByKind = "day"

    @model_validator(mode="after")
    def _validate_range(self) -> "AttendanceStatsFilter":
        if self.dateFrom > self.dateTo:
            raise ValueError("dateFrom doit être <= dateTo")
        if (self.dateTo - self.dateFrom) > timedelta(days=366):
            raise ValueError("La période ne peut pas excéder 366 jours")
        if not any([self.schoolId, self.classRoomId, self.studentId]):
            raise ValueError(
                "Au moins un de schoolId/classRoomId/studentId est requis"
            )
        return self


class AttendanceStatsPoint(BaseModel):
    """Un point sur la série temporelle (bucket = day | week | month)."""
    date: date
    present: int = 0
    absent: int = 0
    late: int = 0


class AttendanceStatsTotals(BaseModel):
    """Totaux globaux sur la période sélectionnée."""
    present: int = 0
    absent: int = 0
    late: int = 0
    total: int = 0


class AttendanceStatsPeriod(BaseModel):
    """Bornes effectives utilisées (utile pour le client : confirme la coupe)."""
    dateFrom: date
    dateTo: date
    groupBy: GroupByKind


class AttendanceStatsResponse(BaseModel):
    """Réponse de ``GET /api/attendance/stats``."""
    series: list[AttendanceStatsPoint]
    totals: AttendanceStatsTotals
    attendanceRate: float = Field(ge=0.0, le=1.0)
    period: AttendanceStatsPeriod


# ---------------------------------------------------------------------------
# Module 3 — partitions
# ---------------------------------------------------------------------------
class PartitionInfo(BaseModel):
    """Métadonnées d'une partition mensuelle de ``AttendanceRecord``."""
    name: str
    start: date
    end: date
    rowCount: int
    sizeMb: float


class EnsurePartitionsResponse(BaseModel):
    """Réponse de ``POST /api/attendance/partitions/ensure``."""
    created: list[str]
    already_present: list[str]
