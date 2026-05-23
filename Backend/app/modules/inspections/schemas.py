"""Pydantic schemas pour le module Inspections."""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import (
    ActionItemStatus,
    FindingSeverity,
    InspectionCriterion,
    InspectionStatus,
)


# =============================================================
# REQUESTS
# =============================================================
class CreateInspectionRequest(BaseModel):
    """POST /api/inspections — planifier une visite."""

    model_config = ConfigDict(str_strip_whitespace=True)

    schoolId: str
    inspectorId: str | None = None  # default: caller
    scheduledDate: date
    notes: str | None = Field(default=None, max_length=2000)


class UpdateInspectionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: InspectionStatus | None = None
    performedDate: date | None = None
    notes: str | None = Field(default=None, max_length=2000)


class CreateFindingRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    criterion: InspectionCriterion
    score: int = Field(ge=0, le=5)
    severity: FindingSeverity = FindingSeverity.INFO
    comment: str | None = Field(default=None, max_length=2000)
    photoUrl: str | None = None


class CreateActionItemRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(min_length=3, max_length=2000)
    dueDate: date


class UpdateActionItemRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: ActionItemStatus
    resolutionNote: str | None = Field(default=None, max_length=2000)


# =============================================================
# RESPONSES
# =============================================================
class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    criterion: InspectionCriterion
    score: int
    severity: FindingSeverity
    comment: str | None = None
    photoUrl: str | None = None
    createdAt: datetime


class ActionItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    description: str
    dueDate: datetime
    status: ActionItemStatus
    resolvedAt: datetime | None = None
    resolvedById: str | None = None
    createdAt: datetime
    updatedAt: datetime


class InspectorBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    fullName: str
    email: str


class SchoolBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str


class InspectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schoolId: str
    school: SchoolBrief | None = None
    inspectorId: str
    inspector: InspectorBrief | None = None
    scheduledDate: datetime
    performedDate: datetime | None = None
    status: InspectionStatus
    overallScore: float | None = None
    notes: str | None = None
    findings: list[FindingRead] = []
    actionItems: list[ActionItemRead] = []
    createdAt: datetime
    updatedAt: datetime


class InspectionListItem(BaseModel):
    """Vue compacte pour la liste paginée."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    schoolId: str
    school: SchoolBrief | None = None
    inspectorId: str
    inspector: InspectorBrief | None = None
    scheduledDate: datetime
    performedDate: datetime | None = None
    status: InspectionStatus
    overallScore: float | None = None
    findingsCount: int = 0
    actionItemsOpen: int = 0


class InspectionPage(BaseModel):
    rows: list[InspectionListItem]
    total: int
    page: int
    pageSize: int


class InspectionStats(BaseModel):
    """GET /api/inspections/stats — synthèse pour pilotage."""
    total: int
    planned: int
    inProgress: int
    completed: int
    cancelled: int
    averageScoreLast90Days: float | None = None
    criticalFindingsLast90Days: int = 0
    overdueActions: int = 0
