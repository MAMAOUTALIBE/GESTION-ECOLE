"""Module 1A — Schemas Pydantic du module Enrollment.

On expose des noms camelCase pour rester aligné avec le frontend Angular
existant et les autres modules du projet.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.modules.enrollment.enums import EnrollmentClassLevel, EnrollmentSource
from app.shared.enums import Gender


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class EnrollmentCreate(BaseModel):
    """Saisie unitaire d'un effectif désagrégé."""

    model_config = ConfigDict(str_strip_whitespace=True)

    schoolYearId: str = Field(max_length=30)
    schoolId: str = Field(max_length=30)
    classLevel: EnrollmentClassLevel
    gender: Gender
    count: int = Field(ge=0, le=100_000)
    source: EnrollmentSource = EnrollmentSource.CENSUS_DECLARED
    notes: str | None = Field(default=None, max_length=500)


class EnrollmentBulkCreate(BaseModel):
    """Saisie groupée — max 200 lignes pour éviter une saturation transaction."""

    items: list[EnrollmentCreate] = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class EnrollmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    schoolYearId: str
    schoolId: str
    classLevel: EnrollmentClassLevel
    gender: Gender
    count: int
    source: EnrollmentSource
    recordedAt: datetime
    recordedById: str | None
    notes: str | None
    createdAt: datetime
    updatedAt: datetime


class BulkItemError(BaseModel):
    index: int
    message: str


class BulkRecordResponse(BaseModel):
    inserted: int
    errors: list[BulkItemError]


# ---------------------------------------------------------------------------
# Aggregate API
# ---------------------------------------------------------------------------
class AggregateScope(StrEnum):
    """Granularité d'agrégation pour ``/aggregate``."""

    NATIONAL = "NATIONAL"
    REGIONAL = "REGIONAL"
    PREFECTURE = "PREFECTURE"
    SUBPREFECTURE = "SUBPREFECTURE"
    SCHOOL = "SCHOOL"


class AggregateRequest(BaseModel):
    """Filtres d'agrégation. ``scope`` détermine le niveau territorial cible
    et les filtres ``*Id`` agissent en condition supplémentaire (intersection).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    scope: AggregateScope = AggregateScope.NATIONAL
    schoolYearId: str = Field(max_length=30)
    regionId: str | None = Field(default=None, max_length=30)
    prefectureId: str | None = Field(default=None, max_length=30)
    subPrefectureId: str | None = Field(default=None, max_length=30)
    schoolId: str | None = Field(default=None, max_length=30)
    classLevel: EnrollmentClassLevel | None = None
    gender: Gender | None = None
    source: EnrollmentSource = EnrollmentSource.CENSUS_DECLARED


class EnrollmentAggregate(BaseModel):
    """Une cellule d'agrégat : un (niveau, genre) → effectif total.

    ``gpi`` (Gender Parity Index) est calculé côté cellule "niveau" agrégé
    par genre uniquement ; pour une cellule pleinement désagrégée, il vaut
    None.
    """

    level: EnrollmentClassLevel | None = None
    gender: Gender | None = None
    count: int
    gpi: float | None = None


class AggregateResponse(BaseModel):
    scope: AggregateScope
    schoolYearId: str
    total: int
    byLevel: list[EnrollmentAggregate]
    byGender: list[EnrollmentAggregate]
    breakdown: list[EnrollmentAggregate]


__all__ = [
    "AggregateRequest",
    "AggregateResponse",
    "AggregateScope",
    "BulkItemError",
    "BulkRecordResponse",
    "EnrollmentAggregate",
    "EnrollmentBulkCreate",
    "EnrollmentCreate",
    "EnrollmentRead",
]
