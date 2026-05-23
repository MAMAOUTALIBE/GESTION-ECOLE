"""Pydantic schemas for the census module — mirror NestJS census.service.ts
response shapes (mapStudent / mapTeacher / dashboard).
"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.schools.schemas import (
    SchoolEmbedded,
    TerritorialBriefRead,
)
from app.modules.territory.schemas import (
    PrefectureRead,
    RegionRead,
    SubPrefectureRead,
)
from app.shared.enums import (
    AttendanceStatus,
    Gender,
    PersonType,
    ValidationStatus,
)


# =============================================================
# REQUESTS
# =============================================================
class CreateStudentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    firstName: str = Field(min_length=2)
    lastName: str = Field(min_length=2)
    gender: Gender
    photoUrl: str | None = None
    birthDate: date | None = None
    guardianName: str | None = None
    guardianPhone: str | None = None
    schoolId: str
    classRoomId: str | None = None


class CreateTeacherRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    firstName: str = Field(min_length=2)
    lastName: str = Field(min_length=2)
    gender: Gender
    photoUrl: str | None = None
    birthDate: date | None = None
    phone: str | None = None
    subject: str | None = None
    diploma: str | None = None
    schoolId: str
    classRoomIds: list[str] | None = None


class AssignStudentClassRequest(BaseModel):
    classRoomId: str | None = None


class AssignTeacherClassesRequest(BaseModel):
    classRoomIds: list[str]


class TransferStudentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    toSchoolId: str
    toClassRoomId: str | None = None
    reason: str | None = None


class DashboardQuery(BaseModel):
    """Query params for GET /api/census/dashboard."""
    model_config = ConfigDict(str_strip_whitespace=True)

    regionId: str | None = None
    prefecture: str | None = None
    commune: str | None = None
    schoolId: str | None = None


# =============================================================
# RESPONSES
# =============================================================
class ClassRoomSummary(BaseModel):
    """ClassRoom shape used in mapStudent/mapTeacher payloads."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    level: str | None = None
    maxStudents: int | None = None
    schoolYear: str | None = None
    schoolId: str
    school: SchoolEmbedded | None = None
    createdAt: datetime
    updatedAt: datetime


class TransferHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    transferredAt: datetime
    reason: str | None = None
    fromSchool: SchoolEmbedded | None = None
    toSchool: SchoolEmbedded | None = None
    fromClassRoom: ClassRoomSummary | None = None
    toClassRoom: ClassRoomSummary | None = None
    actor: dict | None = None  # { id, fullName, email } or None


class StudentRead(BaseModel):
    """mirror NestJS mapStudent()."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: Literal[PersonType.STUDENT] = PersonType.STUDENT
    uniqueCode: str
    firstName: str
    lastName: str
    fullName: str
    gender: Gender
    birthDate: datetime | None = None
    photoUrl: str | None = None
    guardianName: str | None = None
    guardianPhone: str | None = None
    school: SchoolEmbedded | None = None
    classRoom: ClassRoomSummary | None = None
    transferHistory: list[TransferHistoryItem] | None = None
    qrToken: str | None = None
    qrPayload: str
    qrSvg: str | None = None
    createdAt: datetime


class TeacherRead(BaseModel):
    """mirror NestJS mapTeacher()."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: Literal[PersonType.TEACHER] = PersonType.TEACHER
    uniqueCode: str
    firstName: str
    lastName: str
    fullName: str
    gender: Gender
    birthDate: datetime | None = None
    photoUrl: str | None = None
    phone: str | None = None
    subject: str | None = None
    diploma: str | None = None
    status: ValidationStatus
    rejectionReason: str | None = None
    school: SchoolEmbedded | None = None
    classes: list[ClassRoomSummary] = []
    qrToken: str | None = None
    qrPayload: str
    qrSvg: str | None = None
    createdAt: datetime


# --- DASHBOARD --------------------------------------------------------
class DashboardTotals(BaseModel):
    students: int
    teachers: int
    schools: int
    classes: int
    regions: int
    presentToday: int
    attendanceToday: int
    registeredPeople: int


class DashboardByRegion(BaseModel):
    id: str
    name: str
    schools: int
    students: int
    teachers: int


class DashboardByTerritory(BaseModel):
    id: str
    name: str
    region: TerritorialBriefRead
    schools: int
    students: int
    teachers: int
    classes: int
    geolocatedSchools: int
    studentsPerTeacher: float
    gpsCoverageRate: int


class DashboardRatios(BaseModel):
    studentsPerTeacher: float
    studentsPerSchool: float
    teachersPerSchool: float
    averageClassSize: float


class DashboardCapacity(BaseModel):
    classCapacity: int
    assignedStudents: int
    fillRate: int
    overloadedClasses: int
    studentsWithoutClass: int


class DashboardDataQuality(BaseModel):
    score: int
    studentsWithoutClass: int
    studentsWithoutPhoto: int
    studentsMissingBirthDate: int
    teachersWithoutClasses: int
    teachersWithoutPhoto: int
    teachersMissingBirthDate: int
    schoolsWithoutCoordinates: int
    schoolsMissingPhone: int


class DashboardTerritory(BaseModel):
    prefectures: int
    communes: int
    geolocatedSchools: int
    gpsCoverageRate: int


class DashboardAlert(BaseModel):
    level: Literal["success", "info", "warning", "danger"]
    title: str
    description: str


class DashboardTopSchool(BaseModel):
    id: str
    name: str
    code: str
    region: TerritorialBriefRead
    students: int
    teachers: int
    classes: int


class DashboardOverloadedClass(BaseModel):
    id: str
    name: str
    level: str | None = None
    school: dict | None = None
    students: int
    maxStudents: int | None = None


class RecentAttendance(BaseModel):
    id: str
    personType: PersonType
    status: AttendanceStatus
    scannedAt: datetime
    person: StudentRead | TeacherRead | None = None


class IdentifyResponse(BaseModel):
    """GET /api/census/identify/{token}."""
    personType: PersonType
    person: StudentRead | TeacherRead | None = None


class QrSvgResponse(BaseModel):
    """GET /api/census/qr/{token}."""
    personType: PersonType
    person: StudentRead | TeacherRead | None = None
    qrSvg: str | None = None


class DashboardResponse(BaseModel):
    totals: DashboardTotals
    filters: DashboardQuery
    byRegion: list[DashboardByRegion]
    byPrefecture: list[DashboardByTerritory]
    byCommune: list[DashboardByTerritory]
    ratios: DashboardRatios
    capacity: DashboardCapacity
    dataQuality: DashboardDataQuality
    territory: DashboardTerritory
    operationalAlerts: list[DashboardAlert]
    topSchools: list[DashboardTopSchool]
    overloadedClasses: list[DashboardOverloadedClass]
    recentAttendances: list[RecentAttendance]


# --- METADATA ---------------------------------------------------------
class MetadataResponse(BaseModel):
    """GET /api/census/metadata — bundle of regions/schools/prefectures."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    regions: list[RegionRead]
    schools: list[SchoolEmbedded]
    prefectures: list[PrefectureRead]
    subPrefectures: list[SubPrefectureRead]
    roles: list[str]
