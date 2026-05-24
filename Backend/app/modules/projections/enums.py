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


__all__ = [
    "BASELINE_SCENARIO_ID",
    "DEMOGRAPHIC_GROWTH_RATE_DEFAULT",
    "TransitionScope",
]
