"""Module 2A + 2B — Schemas Pydantic du module Projections.

Noms ``camelCase`` pour rester aligné avec le frontend Angular et les
autres modules du projet.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.enrollment.enums import EnrollmentClassLevel
from app.modules.projections.enums import (
    BASELINE_SCENARIO_ID,
    TransitionScope,
)
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


# ===========================================================================
# Module 2B — Projection horizon 5 ans
# ===========================================================================
class RunProjectionRequest(BaseModel):
    """Demande de calcul d'une projection horizon multi-années.

    * ``baseSchoolYearId`` : année source des effectifs initiaux
      (CENSUS_DECLARED) sur laquelle on applique les rates.
    * ``horizonYears`` : nombre d'années à projeter. Plafonné à 10 :
      au-delà, la propagation des incertitudes rend la projection
      inutilisable pour le pilotage.
    * ``scenarioId`` : id du ``ProjectionScenario`` à utiliser. Défaut
      ``"BASELINE"`` (seedé par la migration 0027).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    baseSchoolYearId: str = Field(min_length=1, max_length=30)
    horizonYears: int = Field(default=5, ge=1, le=10)
    scenarioId: str = Field(
        default=BASELINE_SCENARIO_ID, min_length=1, max_length=30,
    )


class RunProjectionResponse(BaseModel):
    """Métadonnées renvoyées par ``POST /projections/run``."""

    scenarioId: str
    projectedRows: int = 0
    regionsCovered: int = 0
    horizonYears: int = 0
    computedAt: datetime


class ProjectionFilters(BaseModel):
    """Filtres pour ``GET /projections``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    baseSchoolYearId: str | None = Field(default=None, max_length=30)
    projectedYear: int | None = None
    scope: TransitionScope | None = None
    entityId: str | None = Field(default=None, max_length=30)
    classLevel: EnrollmentClassLevel | None = None
    gender: Gender | None = None
    scenarioId: str | None = Field(default=None, max_length=30)
    # Pagination (offset-based ; offre la simplicité côté front
    # Angular et reste plus que suffisant pour ~ quelques milliers de rows).
    limit: int = Field(default=200, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class ProjectedEnrollmentRead(BaseModel):
    """Représentation d'un ``ProjectedEnrollment`` pour l'API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    baseSchoolYearId: str
    projectedYear: int
    scope: TransitionScope
    entityId: str | None = None
    classLevel: EnrollmentClassLevel
    gender: Gender
    projectedCount: int
    computedAt: datetime
    scenarioId: str


class ProjectionAggregate(BaseModel):
    """Représentation agrégée (pour vue dashboard rollup)."""

    model_config = ConfigDict(from_attributes=True)

    year: int
    scope: TransitionScope
    entityId: str | None = None
    classLevel: EnrollmentClassLevel
    gender: Gender
    projectedCount: int


class ProjectionScenarioCreate(BaseModel):
    """Création d'un scénario de projection.

    ``demographicGrowthRate`` est borné à ``[-0.5, 0.5]`` (entre -50 % et
    +50 % par an) — au-delà la projection n'a aucun sens et signale
    probablement une erreur de saisie.

    ``customTransitionRates`` est laissé libre côté JSONB : la validation
    fine (clé "CP1->CP2:FEMALE", valeurs entre 0 et 2) est faite par le
    service métier pour rester souple.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    demographicGrowthRate: Decimal | None = Field(
        default=None, ge=Decimal("-0.5"), le=Decimal("0.5"),
    )
    customTransitionRates: dict[str, Any] | None = None


class ProjectionScenarioRead(BaseModel):
    """Représentation d'un ``ProjectionScenario`` pour l'API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    demographicGrowthRate: Decimal
    customTransitionRates: dict[str, Any] | None = None
    createdById: str | None = None
    createdAt: datetime


__all__ = [
    "LEVEL_PAIRS",
    "LEVEL_SEQUENCE",
    "ComputeTransitionsRequest",
    "ComputeTransitionsResponse",
    "ProjectedEnrollmentRead",
    "ProjectionAggregate",
    "ProjectionFilters",
    "ProjectionScenarioCreate",
    "ProjectionScenarioRead",
    "RunProjectionRequest",
    "RunProjectionResponse",
    "TransitionRateFilters",
    "TransitionRateRead",
]
