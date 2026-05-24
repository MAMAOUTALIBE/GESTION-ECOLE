"""Pydantic schemas for the workflow module — validation requests + notifications."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import (
    NotificationType,
    UserRole,
    ValidationEntityType,
    ValidationStatus,
)


# =============================================================
# REQUESTS
# =============================================================
class ReviewValidationRequestPayload(BaseModel):
    """PATCH /api/validation-requests/{id}/review."""
    model_config = ConfigDict(str_strip_whitespace=True)

    status: ValidationStatus
    reason: str | None = Field(default=None, min_length=2)


# =============================================================
# RESPONSES
# =============================================================
class UserBrief(BaseModel):
    """Compact user payload used inside ValidationRequestRead."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    fullName: str
    email: str
    role: UserRole


class ValidationRequestRead(BaseModel):
    """mirror NestJS prisma payload for ValidationRequest with relations."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    entityType: ValidationEntityType
    entityId: str
    status: ValidationStatus
    requestedById: str
    reviewerRole: UserRole
    reviewerRegionId: str | None = None
    reviewerPrefectureId: str | None = None
    reviewerSubPrefectureId: str | None = None
    reviewerUserId: str | None = None
    reason: str | None = None
    reviewedAt: datetime | None = None
    createdAt: datetime
    updatedAt: datetime
    # Module 6 — SLA bookkeeping
    slaDeadline: datetime | None = None
    escalatedAt: datetime | None = None
    escalationLevel: int = 0
    requestedBy: UserBrief | None = None
    reviewer: UserBrief | None = None


class NotificationRead(BaseModel):
    """mirror NestJS prisma payload for Notification (raw row, no relations)."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    recipientUserId: str
    senderUserId: str | None = None
    title: str
    message: str
    type: NotificationType
    entityType: ValidationEntityType | None = None
    entityId: str | None = None
    isRead: bool
    readAt: datetime | None = None
    createdAt: datetime


class UnreadCountResponse(BaseModel):
    count: int
