"""Module 19 — Schemas Pydantic pour l'API cockpit ministériel."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.cockpit.enums import AlertSeverity, KpiKey


class NationalKpiResponse(BaseModel):
    """KPI agrégés nationaux servis par GET /api/cockpit/kpis/national.

    On expose à la fois les valeurs brutes et un format ``items``
    canonique (clé → valeur) pour faciliter l'itération côté frontend.
    Le champ ``nationalGpi`` (Module 1B) est servi en ``Decimal`` pour
    préserver la précision (rapports gouvernementaux).
    """

    studentsTotal: int = 0
    attendanceRate: float = 0.0  # 0..100, pourcentage
    budgetConsumption: float = 0.0  # 0..100, pourcentage
    criticalAnomaliesOpen: int = 0
    alertsOpen: int = 0
    # Module 1B — Gender Parity Index national. None si aucun snapshot
    # n'a encore été calculé (les ops doivent lancer
    # ``compute_gpi_snapshots`` au moins une fois).
    nationalGpi: Decimal | None = None
    items: dict[str, float] = Field(default_factory=dict)
    generatedAt: datetime
    cached: bool = False


class TopAlertSchoolRow(BaseModel):
    schoolId: str
    schoolName: str | None = None
    anomaliesCount: int
    regionId: str | None = None


class TopAlertRegionRow(BaseModel):
    regionId: str
    regionName: str | None = None
    dropoutCount: int


class TopAlertsResponse(BaseModel):
    """Top alertes structurées : écoles + régions."""

    schools: list[TopAlertSchoolRow] = Field(default_factory=list)
    regions: list[TopAlertRegionRow] = Field(default_factory=list)
    generatedAt: datetime


class TimeSeriesPoint(BaseModel):
    date: date
    value: float
    label: str | None = None


class TimeSeriesResponse(BaseModel):
    """Série temporelle d'un KPI."""

    kpiKey: str
    granularity: str  # "DAY" | "WEEK"
    points: list[TimeSeriesPoint] = Field(default_factory=list)
    generatedAt: datetime


class BriefingAlertItem(BaseModel):
    schoolId: str | None = None
    schoolName: str | None = None
    severity: AlertSeverity | None = None
    summary: str


class BriefingResponse(BaseModel):
    """Brief quotidien synthétique servi au cabinet ministre."""

    date: date
    headline: str
    bullets: list[str] = Field(default_factory=list)
    topAlerts: list[BriefingAlertItem] = Field(default_factory=list)
    kpis: dict[str, float] = Field(default_factory=dict)
    source: str  # "llm" | "template"
    generatedAt: datetime


class ComparisonResponse(BaseModel):
    """Variation J/J-1 d'un KPI donné."""

    kpiKey: KpiKey
    today: float
    yesterday: float
    delta: float
    deltaPercent: float
    direction: str  # "up" | "down" | "stable"
    generatedAt: datetime


class SnapshotRunResponse(BaseModel):
    """Réponse du snapshot quotidien (utile pour les tests / dispatcher)."""

    model_config = ConfigDict(from_attributes=True)

    snapshotDate: date
    persisted: int
    keys: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "BriefingAlertItem",
    "BriefingResponse",
    "ComparisonResponse",
    "NationalKpiResponse",
    "SnapshotRunResponse",
    "TimeSeriesPoint",
    "TimeSeriesResponse",
    "TopAlertRegionRow",
    "TopAlertSchoolRow",
    "TopAlertsResponse",
]
