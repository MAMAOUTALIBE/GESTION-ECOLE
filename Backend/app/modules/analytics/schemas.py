"""Pydantic schemas for the analytics module — read-only KPIs + trends."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================
# QUERIES
# =============================================================

TerritoryLevel = Literal["region", "prefecture", "sub-prefecture"]
TopMetric = Literal["students", "attendance", "gps", "ratio"]


class TerritoriesQuery(BaseModel):
    """GET /api/analytics/territories — drill-down level."""

    level: TerritoryLevel = "region"


class TrendsQuery(BaseModel):
    """GET /api/analytics/attendance/trends — sliding window."""

    days: int = Field(default=30, ge=1, le=365)


class EnrollmentTrendsQuery(BaseModel):
    """GET /api/analytics/enrollment/trends — sliding window."""

    months: int = Field(default=12, ge=1, le=60)


class TopSchoolsQuery(BaseModel):
    """GET /api/analytics/top-schools — leaderboard."""

    metric: TopMetric = "students"
    limit: int = Field(default=10, ge=1, le=100)


# =============================================================
# RESPONSES — National KPIs
# =============================================================
class NationalKpis(BaseModel):
    """Top-level numbers for a director's first-page dashboard."""

    students: int
    teachers: int
    schools: int
    classes: int
    regions: int

    studentsPerTeacher: float
    studentsPerSchool: float
    teachersPerSchool: float

    geolocatedSchools: int
    gpsCoverageRate: int  # %, 0..100
    approvedSchools: int
    pendingSchools: int

    attendanceLast7Days: int  # raw scans
    presentLast7Days: int
    absentLast7Days: int
    lateLast7Days: int
    presenceRateLast7Days: float  # %, 0..100

    parentReachable: int          # parents with phone OR email
    parentReachableRate: float    # %, 0..100


# =============================================================
# RESPONSES — Territory comparison
# =============================================================
class TerritoryRow(BaseModel):
    id: str
    name: str
    parentId: str | None = None  # region id when level != region
    parentName: str | None = None
    schools: int
    students: int
    teachers: int
    classes: int
    geolocatedSchools: int
    gpsCoverageRate: int
    studentsPerTeacher: float
    studentsPerSchool: float


class TerritoriesResponse(BaseModel):
    level: TerritoryLevel
    total: int
    rows: list[TerritoryRow]


# =============================================================
# RESPONSES — Trends (time series)
# =============================================================
class AttendancePoint(BaseModel):
    day: date
    present: int
    late: int
    absent: int
    total: int
    presenceRate: float


class AttendanceTrends(BaseModel):
    days: int
    points: list[AttendancePoint]


class EnrollmentPoint(BaseModel):
    month: str  # YYYY-MM
    students: int
    teachers: int


class EnrollmentTrends(BaseModel):
    months: int
    points: list[EnrollmentPoint]


# =============================================================
# RESPONSES — Top schools
# =============================================================
class TopSchoolRow(BaseModel):
    id: str
    code: str
    name: str
    regionId: str | None = None
    regionName: str | None = None
    students: int
    teachers: int
    classes: int
    presenceRateLast7Days: float | None = None
    gpsCoverageRate: int | None = None  # 0/100 per school (it's geolocated or not)


class TopSchoolsResponse(BaseModel):
    metric: TopMetric
    limit: int
    rows: list[TopSchoolRow]


# =============================================================
# RESPONSES — Data quality
# =============================================================
class QualityResponse(BaseModel):
    score: int  # 0..100
    studentsTotal: int
    studentsWithoutClass: int
    studentsWithoutPhoto: int
    studentsMissingBirthDate: int
    teachersTotal: int
    teachersWithoutClasses: int
    teachersWithoutPhoto: int
    teachersMissingBirthDate: int
    schoolsTotal: int
    schoolsMissingCoordinates: int
    schoolsMissingPhone: int


# =============================================================
# AUDIT LOG (cross-cutting observability)
# =============================================================
class AuditLogRow(BaseModel):
    """Single AuditLog entry — flattened for the admin UI."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    actorId: str | None = None
    action: str
    entity: str
    entityId: str | None = None
    metadata: dict | None = Field(default=None, alias="metadata_")
    createdAt: datetime


class AuditLogPage(BaseModel):
    rows: list[AuditLogRow]
    total: int
    page: int
    pageSize: int


class AuditLogQuery(BaseModel):
    """GET /api/audit-logs query params."""

    model_config = ConfigDict(str_strip_whitespace=True)

    actorId: str | None = None
    entity: str | None = None
    entityId: str | None = None
    action: str | None = None
    page: int = 1
    pageSize: int = Field(default=50, ge=1, le=500)


# =============================================================
# Phase 10 — Cohort analysis, equity index, policy simulator
# =============================================================
class CohortLevelStats(BaseModel):
    """Effectifs et flux pour un niveau d'enseignement donné dans une cohorte."""

    level: str
    enrolled: int
    male: int
    female: int
    repeaters: int
    averageAge: float | None = None


class CohortReport(BaseModel):
    """GET /api/analytics/cohorts — vue par niveau d'une cohorte (année scolaire)."""

    schoolYearId: str | None = None
    schoolYearName: str | None = None
    levels: list[CohortLevelStats]
    totalStudents: int
    totalRepeaters: int
    repeaterRate: float


class EquityRow(BaseModel):
    """Index d'équité par territoire (région ou national)."""

    territoryId: str | None = None
    territoryName: str
    students: int
    male: int
    female: int
    genderParityIndex: float
    schoolsTotal: int
    schoolsWithGirlsToilets: int
    girlsToiletsCoverage: int
    schoolsWithElectricity: int
    electricityCoverage: int
    schoolsWithWater: int
    waterCoverage: int


class EquityResponse(BaseModel):
    rows: list[EquityRow]
    nationalGpi: float
    nationalGirlsToiletsCoverage: int
    nationalElectricityCoverage: int
    nationalWaterCoverage: int


class PolicySimulationRequest(BaseModel):
    """POST /api/analytics/policy-simulator — what-if d'une politique."""

    model_config = ConfigDict(str_strip_whitespace=True)

    regionId: str | None = None
    addSchools: int = Field(default=0, ge=0, le=10000)
    addTeachers: int = Field(default=0, ge=0, le=100000)
    addClassrooms: int = Field(default=0, ge=0, le=100000)
    targetGirlsToiletsCoverage: int | None = Field(default=None, ge=0, le=100)
    targetElectricityCoverage: int | None = Field(default=None, ge=0, le=100)
    horizonYears: int = Field(default=5, ge=1, le=20)


class PolicySimulationDelta(BaseModel):
    metric: str
    baseline: float
    scenario: float
    delta: float
    deltaPct: float | None = None
    interpretation: str


class PolicySimulationResponse(BaseModel):
    regionId: str | None = None
    horizonYears: int
    baseline: dict[str, float]
    scenario: dict[str, float]
    deltas: list[PolicySimulationDelta]
    estimatedAdditionalStudentsCovered: int
    estimatedCostUSD: float
    notes: list[str]
