"""Module 2A — Schemas Pydantic du module Projections.

Noms ``camelCase`` pour rester aligné avec le frontend Angular et les
autres modules du projet.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.enrollment.enums import EnrollmentClassLevel
from app.modules.projections.enums import TransitionScope
from app.modules.projections.transitions import (
    LEVEL_PAIRS,
    LEVEL_SEQUENCE,
)
from app.shared.enums import Gender


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class ComputeTransitionsRequest(BaseModel):
    """Demande de recalcul des taux de transition.

    ``schoolYearFromIds`` est la liste d'années sources : pour chacune le
    service cherche la SchoolYear suivante (par startDate) et calcule les
    rates ``year_from → year_to``.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    schoolYearFromIds: list[str] = Field(min_length=1, max_length=20)


class ComputeTransitionsResponse(BaseModel):
    """Retour du POST /transitions/compute."""

    computed: int = 0
    outliers: int = 0
    anomaliesCreated: int = 0
    skipped: list[str] = Field(default_factory=list)
    computedAt: datetime


class TransitionRateFilters(BaseModel):
    """Filtres pour ``GET /transitions``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    scope: TransitionScope | None = None
    entityId: str | None = Field(default=None, max_length=30)
    schoolYearFromId: str | None = Field(default=None, max_length=30)
    classLevelFrom: EnrollmentClassLevel | None = None
    gender: Gender | None = None


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class TransitionRateRead(BaseModel):
    """Représentation d'un ``TransitionRate`` pour l'API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    scope: TransitionScope
    entityId: str | None = None
    schoolYearFromId: str
    schoolYearToId: str
    classLevelFrom: EnrollmentClassLevel
    classLevelTo: EnrollmentClassLevel
    gender: Gender
    rate: Decimal | None = None
    sampleSize: int
    isOutlier: bool
    computedAt: datetime


__all__ = [
    "LEVEL_PAIRS",
    "LEVEL_SEQUENCE",
    "ComputeTransitionsRequest",
    "ComputeTransitionsResponse",
    "TransitionRateFilters",
    "TransitionRateRead",
]
