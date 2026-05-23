"""Pydantic schemas for the attendance module — mirror NestJS mapRecord()."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
