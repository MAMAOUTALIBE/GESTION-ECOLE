"""Module 2A + 2B — Enums du module Projections.

TransitionScope
---------------
Granularité d'un taux de transition stocké et d'une projection :

* ``NATIONAL`` — agrégat pays (entityId NULL).
* ``REGIONAL`` — par région (entityId = regionId).

On limite volontairement à 2 échelons : le taux de transition par
préfecture/école est très bruité (faibles effectifs) et n'apporte pas
de valeur de pilotage IIPE — le cabinet veut un signal national +
régional fiable.

DEMOGRAPHIC_GROWTH_RATE_DEFAULT (Module 2B)
-------------------------------------------
Taux annuel de croissance démographique projeté par défaut pour
MATERNELLE_1 (premier niveau, sans niveau précédent).
Source : Institut national de la statistique de Guinée — 2.4 % par
an. Mutable par scénario (``ProjectionScenario.demographicGrowthRate``).

BASELINE_SCENARIO_ID (Module 2B)
--------------------------------
Identifiant fixe du scénario par défaut, inséré par la migration
0027. Permet d'omettre l'attribut ``scenarioId`` dans les requêtes API
sans avoir à faire un lookup préalable.
"""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum


class TransitionScope(StrEnum):
    """Granularité d'un ``TransitionRate`` ou d'une projection."""

    NATIONAL = "NATIONAL"
    REGIONAL = "REGIONAL"


# Module 2B — Taux INS Guinée 2024 (utilisé pour MATERNELLE_1).
DEMOGRAPHIC_GROWTH_RATE_DEFAULT: Decimal = Decimal("0.0240")

# Module 2B — Identifiant fixe du scénario par défaut.
BASELINE_SCENARIO_ID: str = "BASELINE"


# ===========================================================================
# Module 2C — Capacité vs demande projetée
# ===========================================================================
class CapacityScope(StrEnum):
    """Granularité d'un ``CapacityDemandSnapshot``.

    Quatre échelons cumulatifs :

    * ``SCHOOL``     — entityId = School.id, source primaire (carte scolaire).
    * ``PREFECTURE`` — entityId = Prefecture.id, somme des écoles.
    * ``REGIONAL``   — entityId = Region.id, somme des préfectures.
    * ``NATIONAL``   — entityId NULL, somme des régions.

    On ajoute SCHOOL et PREFECTURE par rapport à TransitionScope (Module 2A/2B)
    parce que la planification infrastructure se pilote précisément à
    l'école et que la préfecture est l'échelon décisionnel des
    investissements MEN.
    """

    NATIONAL = "NATIONAL"
    REGIONAL = "REGIONAL"
    PREFECTURE = "PREFECTURE"
    SCHOOL = "SCHOOL"


class CapacitySeverity(StrEnum):
    """Niveau d'alerte saturation d'une école / agrégat.

    * ``OK``       — saturation <= 80 % : marge suffisante.
    * ``WARNING``  — 80 % < saturation <= 100 % : alerte planification.
    * ``CRITICAL`` — saturation > 100 % : sur-capacité, salles requises.
    """

    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


# Norme MEN Guinée : 50 élèves par salle de classe (cible IIPE 2030).
# Paramétrable par scénario en cas de simulation d'une norme cible plus
# basse (ex. 40 pour s'aligner sur l'objectif Education 2030 UNESCO).
STUDENTS_PER_CLASSROOM_NORM: int = 50

# Seuils de classification de la saturation, en pourcentage.
WARNING_THRESHOLD: Decimal = Decimal("80")
CRITICAL_THRESHOLD: Decimal = Decimal("100")


__all__ = [
    "BASELINE_SCENARIO_ID",
    "CRITICAL_THRESHOLD",
    "DEMOGRAPHIC_GROWTH_RATE_DEFAULT",
    "STUDENTS_PER_CLASSROOM_NORM",
    "WARNING_THRESHOLD",
    "CapacityScope",
    "CapacitySeverity",
    "TransitionScope",
]
