"""Module 5C — Enums + constantes pour l'audit PII."""
from __future__ import annotations

from enum import StrEnum
from typing import Final


class PiiEntityType(StrEnum):
    """Types d'entités PII traçables."""

    STUDENT = "STUDENT"
    PARENT = "PARENT"
    HEALTH_VISIT = "HEALTH_VISIT"
    VACCINATION = "VACCINATION"
    ALLERGY = "ALLERGY"
    INCIDENT = "INCIDENT"
    STUDENT_TRANSFER = "STUDENT_TRANSFER"


class PiiAccessType(StrEnum):
    """Type d'accès consigné."""

    VIEW = "VIEW"       # consultation d'une fiche unique (entityId précis)
    LIST = "LIST"       # apparition dans une liste paginée
    EXPORT = "EXPORT"   # extraction en masse (CSV / Excel / API export)


# Rétention par défaut : 3 ans (RGPD recommandation Art. 5(1)(e)
# minimisation + jurisprudence CNIL sur les logs de traçabilité).
PII_LOG_RETENTION_DAYS: Final[int] = 1095

# Seuil au-delà duquel un endpoint "list" écrit UNE seule ligne agrégée
# (entityId="*", metadataJson={count: N}) au lieu de N lignes — pour éviter
# l'explosion en cas de listing massif (~3M élèves national).
BULK_LIST_AGGREGATION_THRESHOLD: Final[int] = 50


__all__ = [
    "BULK_LIST_AGGREGATION_THRESHOLD",
    "PII_LOG_RETENTION_DAYS",
    "PiiAccessType",
    "PiiEntityType",
]
