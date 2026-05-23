"""Pydantic schemas for the academics module — mirror NestJS academics.service.ts."""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.census.schemas import ClassRoomSummary
from app.modules.schools.schemas import SchoolEmbedded
from app.shared.enums import (
    AcademicPeriodType,
    AcademicValidationStatus,
    AssessmentType,
    CommunicationChannel,
    CommunicationStatus,
    Gender,
    ParentRelationType,
)


# ====================================================================
# REQUESTS
# ====================================================================
class ParentStudentLink(BaseModel):
    studentId: str
    relation: ParentRelationType
    isPrimary: bool = False
    isEmergencyContact: bool = False


class CreateParentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    firstName: str = Field(min_length=2)
    lastName: str = Field(min_length=2)
    phone: str = Field(min_length=6)
    email: EmailStr | None = None
    profession: str | None = None
    address: str | None = None
    preferredLanguage: str | None = None
    links: list[ParentStudentLink] = Field(min_length=1)


class UpdateParentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    firstName: str | None = Field(default=None, min_length=2)
    lastName: str | None = Field(default=None, min_length=2)
    phone: str | None = Field(default=None, min_length=6)
    email: EmailStr | None = None
    profession: str | None = None
    address: str | None = None
    preferredLanguage: str | None = None


class CreateSchoolYearRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=4)
    startDate: date
    endDate: date
    periodType: AcademicPeriodType = AcademicPeriodType.TRIMESTER
    isActive: bool = False


class CreateSubjectRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    code: str = Field(min_length=2)
    name: str = Field(min_length=2)
    level: str | None = None
    coefficient: float = Field(default=1.0, ge=0.1)


class CreateAssessmentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=2)
    type: AssessmentType
    coefficient: float | None = Field(default=None, ge=0.1)
    maxScore: float | None = Field(default=None, ge=1)
    assessedAt: datetime | None = None
    schoolYearId: str
    periodId: str
    subjectId: str
    classRoomId: str
    teacherId: str | None = None


class GradeInput(BaseModel):
    studentId: str
    score: float = Field(ge=0)
    appreciation: str | None = None


class SaveGradesRequest(BaseModel):
    assessmentId: str
    grades: list[GradeInput] = Field(min_length=1)


class GenerateReportCardsRequest(BaseModel):
    schoolYearId: str
    periodId: str
    classRoomId: str | None = None


class UpdateValidationStatusRequest(BaseModel):
    status: AcademicValidationStatus


# ====================================================================
# RESPONSES
# ====================================================================
class StudentBriefForParent(BaseModel):
    """Student summary embedded in a parent payload."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    firstName: str
    lastName: str
    fullName: str
    gender: Gender
    uniqueCode: str
    school: SchoolEmbedded | None = None
    classRoom: ClassRoomSummary | None = None


class ParentStudentLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    relation: ParentRelationType
    isPrimary: bool
    isEmergencyContact: bool
    student: StudentBriefForParent


class CommunicationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel: CommunicationChannel
    status: CommunicationStatus
    subject: str | None = None
    message: str
    sentAt: datetime | None = None
    createdAt: datetime


class ParentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    firstName: str
    lastName: str
    fullName: str
    phone: str
    email: str | None = None
    profession: str | None = None
    address: str | None = None
    preferredLanguage: str | None = None
    otpVerifiedAt: datetime | None = None
    createdAt: datetime
    updatedAt: datetime
    students: list[ParentStudentLinkRead] = []
    communications: list[CommunicationRead] = []


class AcademicPeriodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    type: AcademicPeriodType
    order: int
    startDate: datetime | None = None
    endDate: datetime | None = None
    schoolYearId: str
    createdAt: datetime
    updatedAt: datetime


class SchoolYearRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    startDate: datetime
    endDate: datetime
    periodType: AcademicPeriodType
    isActive: bool
    createdAt: datetime
    updatedAt: datetime
    periods: list[AcademicPeriodRead] = []


class SubjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    level: str | None = None
    coefficient: float
    createdAt: datetime
    updatedAt: datetime


class ClassRoomBriefForAssessment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    level: str | None = None
    schoolId: str
    school: SchoolEmbedded | None = None


class TeacherBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    firstName: str
    lastName: str
    uniqueCode: str
    subject: str | None = None


class AssessmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    type: AssessmentType
    coefficient: float
    maxScore: float
    assessedAt: datetime | None = None
    status: AcademicValidationStatus
    schoolYearId: str
    periodId: str
    subjectId: str
    classRoomId: str
    teacherId: str | None = None
    schoolYear: SchoolYearRead | None = None
    period: AcademicPeriodRead | None = None
    subject: SubjectRead | None = None
    classRoom: ClassRoomBriefForAssessment | None = None
    teacher: TeacherBrief | None = None
    gradesCount: int = 0
    createdAt: datetime
    updatedAt: datetime


class StudentBriefForGrade(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    firstName: str
    lastName: str
    fullName: str
    uniqueCode: str
    school: SchoolEmbedded | None = None
    classRoom: ClassRoomSummary | None = None


class AssessmentBriefForGrade(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    type: AssessmentType
    maxScore: float
    coefficient: float
    subject: SubjectRead | None = None
    period: AcademicPeriodRead | None = None
    classRoomId: str


class GradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    assessmentId: str
    studentId: str
    score: float
    appreciation: str | None = None
    status: AcademicValidationStatus
    recordedAt: datetime
    updatedAt: datetime
    student: StudentBriefForGrade
    assessment: AssessmentBriefForGrade | None = None
    subject: SubjectRead | None = None
    period: AcademicPeriodRead | None = None


class StudentBriefForReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    firstName: str
    lastName: str
    fullName: str
    uniqueCode: str
    school: SchoolEmbedded | None = None
    classRoom: ClassRoomSummary | None = None


class ReportCardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    studentId: str
    classRoomId: str | None = None
    schoolYearId: str
    periodId: str
    average: float | None = None
    rank: int | None = None
    totalStudents: int | None = None
    teacherComment: str | None = None
    directorComment: str | None = None
    verificationCode: str
    status: AcademicValidationStatus
    issuedAt: datetime | None = None
    createdAt: datetime
    updatedAt: datetime
    student: StudentBriefForReport | None = None
    classRoom: ClassRoomBriefForAssessment | None = None
    schoolYear: SchoolYearRead | None = None
    period: AcademicPeriodRead | None = None


class DeletedResponse(BaseModel):
    deleted: bool = True
