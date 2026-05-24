"""Module 3B — Enums du simulateur what-if.

ScenarioStatus
--------------
Workflow d'un scénario :

* ``DRAFT``    — créé, opérations stockées, pas encore calculé.
* ``COMPUTED`` — ``compute_scenario`` a été appelé, ``impactJson`` rempli.
* ``ARCHIVED`` — masqué par défaut (le planificateur l'a "rangé").

OperationType
-------------
Trois types d'opérations sur le réseau :

* ``CREATE_SCHOOL``  — ajoute une école fictive (lat/lon/capacity).
* ``CLOSE_SCHOOL``   — retire une école réelle existante.
* ``MERGE_SCHOOLS``  — fusionne ≥ 2 écoles en une nouvelle ; la capacité
  fusionnée = somme des capacités, position = lat/lon fournis.
"""
from __future__ import annotations

from enum import StrEnum


class ScenarioStatus(StrEnum):
    """Workflow d'un ``SimulationScenario``."""

    DRAFT = "DRAFT"
    COMPUTED = "COMPUTED"
    ARCHIVED = "ARCHIVED"


class OperationType(StrEnum):
    """Type d'opération d'un scénario what-if."""

    CREATE_SCHOOL = "CREATE_SCHOOL"
    CLOSE_SCHOOL = "CLOSE_SCHOOL"
    MERGE_SCHOOLS = "MERGE_SCHOOLS"


__all__ = ["OperationType", "ScenarioStatus"]
