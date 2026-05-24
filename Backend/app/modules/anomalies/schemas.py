"""Module 9 — Schemas Pydantic pour l'API anomalies."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.anomalies.enums import (
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
)


class AnomalyRead(BaseModel):
    """Représentation publique d'une anomalie."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    type: AnomalyType
    severity: AnomalySeverity
    status: AnomalyStatus
    entityType: str
    entityId: str
    description: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    schoolId: str | None = None
    regionId: str | None = None
    detectedAt: datetime
    reviewedAt: datetime | None = None
    reviewedById: str | None = None
    reviewNote: str | None = None


class AnomalyListResponse(BaseModel):
    """Réponse paginée pour GET /api/anomalies."""

    items: list[AnomalyRead]
    total: int
    page: int
    pageSize: int


class AnomalyReviewRequest(BaseModel):
    """Payload du POST /api/anomalies/{id}/review.

    ``status`` doit être l'un des statuts finaux (pas PENDING).
    ``note`` optionnelle mais fortement conseillée pour CONFIRMED /
    FALSE_POSITIVE (sert d'audit trail).
    """

    status: AnomalyStatus
    note: str | None = None


class AnomalyRunResponse(BaseModel):
    """Réponse du POST /api/anomalies/run."""

    detected: int
    schoolId: str | None = None
    ranAt: datetime


class AnomalyStatsByType(BaseModel):
    type: AnomalyType
    count: int


class AnomalyStatsBySeverity(BaseModel):
    severity: AnomalySeverity
    count: int


class AnomalyStats(BaseModel):
    """KPI agrégés pour GET /api/anomalies/stats."""

    total: int
    pending: int
    confirmed: int
    dismissed: int
    falsePositive: int
    byType: list[AnomalyStatsByType] = Field(default_factory=list)
    bySeverity: list[AnomalyStatsBySeverity] = Field(default_factory=list)
    confirmationRate: float = 0.0  # confirmed / (confirmed + dismissed + falsePositive)
