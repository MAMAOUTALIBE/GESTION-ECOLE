"""Module 8 — Predictions ML : enums dédiés."""
from enum import StrEnum


class DropoutRiskLevel(StrEnum):
    """Niveau de risque d'abandon scolaire à 90 jours.

    Seuils MVP (cf. ``DropoutModel.predict_proba``) :
        proba < 0.30        => LOW
        0.30 <= proba <= 0.65 => MEDIUM
        proba > 0.65        => HIGH
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
