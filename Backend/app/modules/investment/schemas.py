"""Module 3C — Schemas Pydantic du score d'investissement.

Convention camelCase pour rester aligné avec le frontend Angular et le
reste de la codebase (Prisma legacy).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.investment.enums import PriorityCategory


class ScoreBreakdown(BaseModel):
    """Détail audit par dimension (stocké dans ``breakdownJson``)."""

    model_config = ConfigDict(extra="allow")

    infrastructure: dict[str, Any] = Field(default_factory=dict)
    saturation: dict[str, Any] = Field(default_factory=dict)
    equity: dict[str, Any] = Field(default_factory=dict)
    accessibility: dict[str, Any] = Field(default_factory=dict)


class InvestmentScoreRead(BaseModel):
    """Sortie ``GET /api/investment/priorities/{schoolId}`` et listing."""

    model_config = ConfigDict(from_attributes=True)

    schoolId: str
    schoolName: str | None = None
    regionId: str | None = None
    regionName: str | None = None
    baseSchoolYearId: str
    infrastructureScore: int
    saturationScore: int
    equityScore: int
    accessibilityScore: int
    totalScore: int
    priorityCategory: PriorityCategory
    computedAt: datetime
    breakdownJson: Any | None = None


class ComputeScoresRequest(BaseModel):
    """Payload ``POST /api/investment/compute-scores``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    baseSchoolYearId: str = Field(min_length=1, max_length=30)


class ComputeScoresResponse(BaseModel):
    """Métadonnées retournées après un recalcul global."""

    scoresComputed: int
    byCategory: dict[str, int] = Field(default_factory=dict)
    baseSchoolYearId: str
    computedAt: datetime


__all__ = [
    "ComputeScoresRequest",
    "ComputeScoresResponse",
    "InvestmentScoreRead",
    "ScoreBreakdown",
]
