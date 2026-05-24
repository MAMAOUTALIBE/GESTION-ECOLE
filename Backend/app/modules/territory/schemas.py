"""Pydantic schemas for the territory module.

Mirror NestJS contracts (CreatePrefectureDto, CreateSubPrefectureDto + Prisma
includes used in territory.service.ts list responses).
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import ValidationStatus, ZoneType


# --- Requests ---
class CreatePrefectureRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2)
    code: str = Field(min_length=2)
    regionId: str | None = None


class CreateSubPrefectureRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2)
    code: str = Field(min_length=2)
    prefectureId: str
    regionId: str | None = None


# --- Embedded responses ---
class RegionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str
    createdAt: datetime
    updatedAt: datetime


class PrefectureCounts(BaseModel):
    """Mirrors Prisma `_count: { subPrefectures, schools, users }`."""
    subPrefectures: int = 0
    schools: int = 0
    users: int = 0


class SubPrefectureCounts(BaseModel):
    """Mirrors Prisma `_count: { schools, users }`."""
    schools: int = 0
    users: int = 0


class PrefectureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str
    regionId: str
    status: ValidationStatus
    rejectionReason: str | None = None
    createdById: str | None = None
    approvedById: str | None = None
    approvedAt: datetime | None = None
    createdAt: datetime
    updatedAt: datetime
    region: RegionRead | None = None


class PrefectureListItem(PrefectureRead):
    """Includes Prisma's `_count` aggregate."""
    _count: PrefectureCounts = PrefectureCounts()


class PrefectureNested(BaseModel):
    """Embeds region into prefecture for sub-prefecture responses."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str
    regionId: str
    region: RegionRead | None = None


class SubPrefectureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str
    regionId: str
    prefectureId: str
    status: ValidationStatus
    rejectionReason: str | None = None
    createdById: str | None = None
    approvedById: str | None = None
    approvedAt: datetime | None = None
    createdAt: datetime
    updatedAt: datetime
    prefecture: PrefectureNested | None = None
    # Module 1C — zone déclarée par l'INS. Toujours non-null en base (default
    # serveur RURAL pose en migration 0025).
    defaultZoneType: ZoneType = ZoneType.RURAL


class SubPrefectureListItem(SubPrefectureRead):
    _count: SubPrefectureCounts = SubPrefectureCounts()


# ---------------------------------------------------------------------------
# Module 1C — Schémas zone urbain / rural
# ---------------------------------------------------------------------------
class SetZoneTypeRequest(BaseModel):
    """Body de PUT /territory/sub-prefectures/{id}/zone-type."""

    zoneType: ZoneType


class SubPrefectureZoneItem(BaseModel):
    """Une ligne de GET /territory/sub-prefectures/zones.

    Inclut le compteur d'écoles dans chaque zone (utile pour l'INS pour
    valider la cohérence du référentiel).
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    code: str
    prefectureId: str
    regionId: str
    defaultZoneType: ZoneType
    # Décompte effectif par zone (override appliqué).
    urbanSchoolsCount: int = 0
    ruralSchoolsCount: int = 0
    periUrbanSchoolsCount: int = 0
    totalSchoolsCount: int = 0
