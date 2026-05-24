"""Module 8 — Schemas Pydantic pour l'API predictions."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.predictions.enums import DropoutRiskLevel


class DropoutPredictionRead(BaseModel):
    """Représentation publique d'un score de risque calculé."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    studentId: str
    schoolYearId: str | None = None
    computedAt: datetime
    probability: float
    riskLevel: DropoutRiskLevel
    featuresSnapshot: dict[str, Any] = Field(default_factory=dict)
    modelVersion: str


class BatchPredictResponse(BaseModel):
    """Réponse 202 du batch predict (async).

    En MVP single-instance on exécute synchronement et on renvoie quand même
    un ``task_id`` (= placeholder) pour rester compatible avec le frontend
    qui pollerait.
    """

    accepted: bool = True
    schoolId: str
    taskId: str
    predicted: int = 0


class ModelInfoResponse(BaseModel):
    """Réponse du GET /model/info — métadonnées du modèle courant."""

    version: str | None
    trainedAt: datetime | None
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifactPath: str | None
    loaded: bool


class TrainModelResponse(BaseModel):
    version: str
    metrics: dict[str, Any] = Field(default_factory=dict)
