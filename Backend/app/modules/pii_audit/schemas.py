"""Module 5C — Schémas Pydantic d'API pour l'audit PII."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.pii_audit.enums import PiiAccessType, PiiEntityType


class PiiAccessLogEntry(BaseModel):
    """Représentation d'une ligne d'audit en réponse API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    userId: str | None = None
    userRole: str | None = None
    entityType: PiiEntityType
    entityId: str
    accessType: PiiAccessType
    endpoint: str
    ip: str | None = None
    userAgent: str | None = None
    requestId: str | None = None
    metadataJson: Any | None = None
    accessedAt: datetime


class PiiAccessLogFilters(BaseModel):
    """Filtres de listing — tous optionnels.

    Le service applique en plus le filtre RBAC (non-admin n'a accès
    qu'à ses propres lignes).
    """

    entityType: PiiEntityType | None = None
    entityId: str | None = Field(default=None, max_length=30)
    userId: str | None = Field(default=None, max_length=30)
    fromDate: datetime | None = None
    toDate: datetime | None = None
    accessType: PiiAccessType | None = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class PurgeRequest(BaseModel):
    """Demande de purge — l'admin choisit la date butoir."""

    cutoffDate: datetime


class PurgeResponse(BaseModel):
    deleted: int
    cutoffDate: datetime


__all__ = [
    "PiiAccessLogEntry",
    "PiiAccessLogFilters",
    "PurgeRequest",
    "PurgeResponse",
]
