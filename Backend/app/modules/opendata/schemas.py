"""Module 12 — Pydantic schemas exposés par le router public.

Tous les schemas sont volontairement **plats et publics** : aucun champ
nominal ne doit y figurer. La validation est faite côté tests via
:func:`app.modules.opendata.anonymization.is_anonymous`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DatasetMetadata(BaseModel):
    """Métadonnées d'un dataset publié — apparaît dans le catalogue.

    On expose seulement ce qui est utile à un consommateur externe.
    Pas de ``createdAt`` interne, pas d'ID DB (uniquement ``key``).
    """

    model_config = ConfigDict(from_attributes=True)

    key: str
    title: str
    description: str
    license: str = "CC-BY-4.0"
    refreshFrequency: str = "daily"
    schemaJsonb: dict[str, Any] = Field(default_factory=dict)
    lastRefreshedAt: datetime | None = None
    recordCount: int | None = None
    sizeKb: int | None = None


class DatasetListResponse(BaseModel):
    """Réponse du catalogue : liste de datasets + count."""

    items: list[DatasetMetadata]
    total: int


class OpendataStats(BaseModel):
    """Statistiques agrégées des téléchargements (anonymes).

    Volontairement minimal : pas d'IP hash exposé, pas de granularité
    temporelle (la fenêtre par défaut est "tout l'historique"). Si
    quelqu'un veut une granularité fine il devra ouvrir un dataset
    dédié.
    """

    totalDownloads: int
    downloadsByDataset: dict[str, int]
    downloadsByFormat: dict[str, int]


__all__ = [
    "DatasetListResponse",
    "DatasetMetadata",
    "OpendataStats",
]
