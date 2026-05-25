"""Module 5D — Schémas Pydantic d'API du droit à l'oubli."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.erasure.enums import ErasureReason, ErasureStatus


class ErasureRequestCreate(BaseModel):
    """Payload de création d'une demande.

    * ``studentId`` — id de l'élève concerné. La validation existence se
      fait côté service.
    * ``reason`` — motif légal (voir ``ErasureReason``).
    * ``reasonDetails`` — texte libre optionnel (obligatoire si reason
      == OTHER côté UI, mais non bloqué côté API pour rester souple).
    """

    studentId: str = Field(..., min_length=1, max_length=30)
    reason: ErasureReason
    reasonDetails: str | None = Field(default=None, max_length=2000)


class CancelErasureRequest(BaseModel):
    """Payload d'annulation pendant la grace period."""

    cancellationReason: str = Field(..., min_length=1, max_length=2000)


class ErasureRequestRead(BaseModel):
    """Représentation API d'une demande.

    ``studentInitials`` est calculé côté service (ex: "M.K.") avant
    qu'on retourne le DTO — il permet aux admins de différencier
    visuellement des demandes sans révéler l'identité complète une
    fois la demande EXECUTED.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    studentId: str | None
    studentInitials: str | None = None
    reason: ErasureReason
    reasonDetails: str | None = None
    status: ErasureStatus
    requestedAt: datetime
    requestedById: str | None = None
    gracePeriodUntil: datetime
    executedAt: datetime | None = None
    executedById: str | None = None
    cancelledAt: datetime | None = None
    cancelledById: str | None = None
    cancellationReason: str | None = None


class ExecutePendingResponse(BaseModel):
    """Réponse du batch d'exécution."""

    executed: int
    skipped: int


__all__ = [
    "CancelErasureRequest",
    "ErasureRequestCreate",
    "ErasureRequestRead",
    "ExecutePendingResponse",
]
