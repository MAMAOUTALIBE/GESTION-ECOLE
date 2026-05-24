from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, confloat

from app.modules.territory.schemas import RegionRead
from app.shared.enums import (
    BuildingCondition,
    ElectricitySource,
    SchoolAffiliation,
    ValidationStatus,
    WaterSource,
    ZoneType,
)


# --- Requests -----------------------------------------------------------
class CreateSchoolRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2)
    code: str = Field(min_length=2)
    regionId: str
    prefectureId: str | None = None
    subPrefectureId: str | None = None
    prefecture: str | None = None
    commune: str | None = None
    type: str | None = None
    address: str | None = None
    phone: str | None = None
    latitude: confloat(ge=-90, le=90) | None = None  # type: ignore[valid-type]
    longitude: confloat(ge=-180, le=180) | None = None  # type: ignore[valid-type]


class UpdateSchoolRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=2)
    code: str | None = Field(default=None, min_length=2)
    regionId: str | None = None
    prefectureId: str | None = None
    subPrefectureId: str | None = None
    prefecture: str | None = None
    commune: str | None = None
    type: str | None = None
    address: str | None = None
    phone: str | None = None
    latitude: confloat(ge=-90, le=90) | None = None  # type: ignore[valid-type]
    longitude: confloat(ge=-180, le=180) | None = None  # type: ignore[valid-type]


class CreateClassRoomRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1)
    level: str | None = None
    maxStudents: int | None = Field(default=None, ge=1)
    schoolYear: str | None = None
    schoolId: str


class UpdateClassRoomRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1)
    level: str | None = None
    maxStudents: int | None = Field(default=None, ge=1)
    schoolYear: str | None = None
    schoolId: str | None = None


# --- Responses ----------------------------------------------------------
class TerritorialBriefRead(BaseModel):
    """Reused for prefectureRef/subPrefecture inside a School payload."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str


class ClassRoomBrief(BaseModel):
    """Class summary nested in school payloads."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    level: str | None = None
    maxStudents: int | None = None
    schoolYear: str | None = None
    schoolId: str
    createdAt: datetime
    updatedAt: datetime


class SchoolCounts(BaseModel):
    classes: int = 0
    students: int = 0
    teachers: int = 0


class SchoolRead(BaseModel):
    """Mirrors NestJS mapSchool() response shape."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str
    regionId: str
    region: RegionRead | None = None
    prefectureId: str | None = None
    subPrefectureId: str | None = None
    prefectureRef: TerritorialBriefRead | None = None
    subPrefecture: TerritorialBriefRead | None = None
    prefecture: str | None = None
    commune: str | None = None
    status: ValidationStatus
    rejectionReason: str | None = None
    type: str | None = None
    address: str | None = None
    phone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    classes: list[ClassRoomBrief] = []
    counts: SchoolCounts = SchoolCounts()
    # Phase 10 — Infrastructure structurée (tous nullables, ajoutés progressivement
    # par les inspecteurs via /api/schools PATCH ou les imports massifs).
    waterSource: WaterSource | None = None
    electricitySource: ElectricitySource | None = None
    internetAvailable: bool = False
    toiletsBoys: int | None = None
    toiletsGirls: int | None = None
    toiletsAccessible: bool = False
    classroomsTotal: int | None = None
    classroomsUsable: int | None = None
    buildingCondition: BuildingCondition | None = None
    buildingYear: int | None = None
    multiShift: bool = False
    distanceToHealthCenterKm: float | None = None
    affiliation: SchoolAffiliation | None = None
    # Module 1C — override zone urbain/rural (NULL = hérite de la sous-préf).
    zoneType: ZoneType | None = None
    createdAt: datetime
    updatedAt: datetime


# ---------------------------------------------------------------------------
# Module 1C — Body de PUT /api/schools/{id}/zone-type
# ---------------------------------------------------------------------------
class SetSchoolZoneTypeRequest(BaseModel):
    """Body de PUT /schools/{id}/zone-type.

    ``zoneType=None`` retire l'override (l'école revient à la valeur INS
    de sa sous-préfecture).
    """

    zoneType: ZoneType | None = None


class SchoolBriefForClass(BaseModel):
    """School summary embedded in a ClassRoom payload (nested region)."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str
    regionId: str
    region: RegionRead | None = None


class SchoolEmbedded(BaseModel):
    """Lightweight school payload for embedding in a Student/Teacher/Transfer.

    Excludes `classes` and `counts` (which would trigger lazy-load errors
    on partially loaded schools).
    """
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str
    regionId: str
    region: RegionRead | None = None
    prefectureId: str | None = None
    subPrefectureId: str | None = None
    prefecture: str | None = None
    commune: str | None = None
    address: str | None = None
    type: str | None = None
    phone: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class ClassRoomRead(BaseModel):
    """Mirrors NestJS mapClassRoom() response shape."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    level: str | None = None
    maxStudents: int | None = None
    schoolYear: str | None = None
    schoolId: str
    school: SchoolBriefForClass | None = None
    studentsCount: int = 0
    teachersCount: int = 0
    createdAt: datetime
    updatedAt: datetime


class DeletedResponse(BaseModel):
    deleted: bool = True
