"""Pydantic schemas for the notifications module — multi-channel parent
communications + dispatch status.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import CommunicationChannel, CommunicationStatus

# =============================================================
# REQUESTS
# =============================================================


class CreateCommunicationRequest(BaseModel):
    """POST /api/communications — queue a single parent communication."""

    model_config = ConfigDict(str_strip_whitespace=True)

    parentId: str
    studentId: str | None = None
    channel: CommunicationChannel
    subject: str | None = Field(default=None, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    sendNow: bool = True  # if False, row is created in DRAFT and not queued


class BulkCommunicationRequest(BaseModel):
    """POST /api/communications/bulk — same message to N parents."""

    model_config = ConfigDict(str_strip_whitespace=True)

    parentIds: list[str] = Field(min_length=1, max_length=5000)
    channel: CommunicationChannel
    subject: str | None = Field(default=None, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    studentId: str | None = None


# =============================================================
# RESPONSES
# =============================================================


class CommunicationRead(BaseModel):
    """Mirror ParentCommunication row + sentAt + status."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    parentId: str
    studentId: str | None = None
    channel: CommunicationChannel
    status: CommunicationStatus
    subject: str | None = None
    message: str
    sentAt: datetime | None = None
    createdAt: datetime


class BulkCommunicationResponse(BaseModel):
    """POST /api/communications/bulk — async ack."""

    queued: int
    taskId: str | None = None  # Celery task id when sendNow=True


class DispatchTestRequest(BaseModel):
    """POST /api/communications/test — fire a one-off message bypassing
    the ParentCommunication table. National admins only.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    channel: CommunicationChannel
    recipient: str = Field(min_length=1)  # phone / email / token / userId
    subject: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class DispatchTestResponse(BaseModel):
    ok: bool
    providerId: str | None = None
    error: str | None = None
