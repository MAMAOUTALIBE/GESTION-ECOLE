"""Module 5D — Enums + constantes pour le droit à l'oubli."""
from __future__ import annotations

from enum import StrEnum
from typing import Final


class ErasureReason(StrEnum):
    """Motif légal de la demande d'anonymisation.

    * ``LEFT_COUNTRY`` — l'élève a déménagé à l'étranger (perte de
      résidence administrative en Guinée).
    * ``DECEASED`` — décès de l'élève. La loi 037/AN/2016 impose le
      retrait des données nominatives à la demande de la famille.
    * ``EXCLUDED`` — exclusion définitive du système scolaire (cas
      rare, généralement enseignement secondaire).
    * ``OTHER`` — motif documenté à expliciter dans ``reasonDetails``.
    """

    LEFT_COUNTRY = "LEFT_COUNTRY"
    DECEASED = "DECEASED"
    EXCLUDED = "EXCLUDED"
    OTHER = "OTHER"


class ErasureStatus(StrEnum):
    """Étapes du workflow d'une demande.

    * ``PENDING`` — valeur DB par défaut (jamais retournée à l'API ;
      la couche service la fait basculer en GRACE_PERIOD avant flush).
    * ``GRACE_PERIOD`` — la demande est créée, 30 jours pour annuler.
    * ``EXECUTED`` — l'anonymisation a été appliquée (irréversible).
    * ``CANCELLED`` — annulée pendant la grace period.
    """

    PENDING = "PENDING"
    GRACE_PERIOD = "GRACE_PERIOD"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"


# Fenêtre de récupération avant exécution effective. Choix de 30 jours :
# court terme par rapport à la loi (2 ans) mais suffisamment long pour
# que toute erreur d'identification soit corrigée avant suppression.
GRACE_PERIOD_DAYS: Final[int] = 30


__all__ = [
    "GRACE_PERIOD_DAYS",
    "ErasureReason",
    "ErasureStatus",
]
