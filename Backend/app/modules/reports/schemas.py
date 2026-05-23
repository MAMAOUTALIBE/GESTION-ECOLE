"""Pydantic schemas for the reports module (PDF bulletins)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import AcademicValidationStatus


class GenerateBulletinsRequest(BaseModel):
    """POST /api/reports/bulletins/generate-batch — queue a batch."""
    schoolYearId: str
    periodId: str
    classRoomId: str | None = None
    reportCardIds: list[str] | None = Field(
        default=None,
        description=(
            "Optional explicit list of report cards. "
            "If omitted, the batch covers all report cards in the (year, period[, class])."
        ),
    )


class BatchAcceptedResponse(BaseModel):
    """202 response when a Celery batch job has been queued."""
    taskId: str
    estimatedItems: int
    message: str = "Bulletins en cours de génération."


class BulletinVerifyResponse(BaseModel):
    """Public lookup by verification code (printed under the QR)."""
    model_config = ConfigDict(from_attributes=True)

    verificationCode: str
    valid: bool
    studentFullName: str | None = None
    schoolName: str | None = None
    periodName: str | None = None
    schoolYearName: str | None = None
    average: float | None = None
    rank: int | None = None
    totalStudents: int | None = None
    status: AcademicValidationStatus | None = None
    issuedAt: datetime | None = None
