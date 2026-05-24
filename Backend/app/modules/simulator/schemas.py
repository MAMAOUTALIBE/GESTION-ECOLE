"""Module 3B — Schemas Pydantic du simulateur what-if.

On utilise une **discriminated union** Pydantic pour les opérations :
chaque op a un champ ``type`` littéral qui détermine le sous-modèle. Cela
donne :

* validation forte côté API (un payload mal formé est rejeté à l'entrée) ;
* type narrowing exploitable dans ``simulator.apply_operations``.

Noms ``camelCase`` pour rester aligné avec le frontend Angular et les
autres modules du projet.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.simulator.enums import OperationType, ScenarioStatus

# ---------------------------------------------------------------------------
# Operations (discriminated union)
# ---------------------------------------------------------------------------


class CreateSchoolOp(BaseModel):
    """Crée une école fictive (lat/lon/capacity).

    Pas de FK ``School`` — l'école n'existe qu'en mémoire dans le scénario.
    Identifiée par un id synthétique côté ``simulator.py``.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    type: Literal[OperationType.CREATE_SCHOOL] = OperationType.CREATE_SCHOOL
    name: str = Field(min_length=1, max_length=200)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    # Capacité directe (places). Permet au planificateur de saisir
    # "200 places" sans devoir convertir en nb de salles × norme.
    capacity: int = Field(ge=0, le=100_000)
    subPrefectureId: str | None = Field(default=None, max_length=30)


class CloseSchoolOp(BaseModel):
    """Ferme une école réelle existante (par id)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    type: Literal[OperationType.CLOSE_SCHOOL] = OperationType.CLOSE_SCHOOL
    schoolId: str = Field(min_length=1, max_length=30)


class MergeSchoolsOp(BaseModel):
    """Fusionne ≥ 2 écoles en une nouvelle école fictive.

    La capacité de la fusion = somme des capacités des sources. Les élèves
    des sources sont redistribués vers la nouvelle école dans le calcul
    d'impact.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    type: Literal[OperationType.MERGE_SCHOOLS] = OperationType.MERGE_SCHOOLS
    sourceSchoolIds: list[str] = Field(min_length=2, max_length=20)
    targetName: str = Field(min_length=1, max_length=200)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    subPrefectureId: str | None = Field(default=None, max_length=30)


Operation = Annotated[
    CreateSchoolOp | CloseSchoolOp | MergeSchoolsOp,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Requests / Responses
# ---------------------------------------------------------------------------


class ScenarioCreate(BaseModel):
    """Payload POST /api/simulator/scenarios."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    baselineSchoolYearId: str = Field(min_length=1, max_length=30)
    operations: list[Operation] = Field(min_length=1, max_length=200)


class CoverageImpact(BaseModel):
    """Couverture du réseau avant/après."""

    beforeCount: int
    afterCount: int
    deltaPct: Decimal


class SaturationImpact(BaseModel):
    """Saturation moyenne et nb d'écoles critiques."""

    beforeAvg: Decimal | None
    afterAvg: Decimal | None
    criticalSchoolsBefore: int
    criticalSchoolsAfter: int


class DistanceImpact(BaseModel):
    """Distance moyenne école-élève (km, estimée par centroid SubPref)."""

    beforeKmMean: Decimal | None
    afterKmMean: Decimal | None
    deltaKm: Decimal | None


class ImpactReport(BaseModel):
    """Rapport d'impact d'un scénario (persisté en ``impactJson``)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    coverage: CoverageImpact
    saturation: SaturationImpact
    distance: DistanceImpact
    redistributedStudents: int


class ScenarioRead(BaseModel):
    """Sortie GET /api/simulator/scenarios/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    status: ScenarioStatus
    createdAt: datetime
    createdById: str
    baselineSchoolYearId: str
    scenarioJson: Any
    impactJson: Any | None = None
    computedAt: datetime | None = None


__all__ = [
    "CloseSchoolOp",
    "CoverageImpact",
    "CreateSchoolOp",
    "DistanceImpact",
    "ImpactReport",
    "MergeSchoolsOp",
    "Operation",
    "SaturationImpact",
    "ScenarioCreate",
    "ScenarioRead",
]
