"""Module 2A — Enums du module Projections.

TransitionScope
---------------
Granularité d'un taux de transition stocké :

* ``NATIONAL`` — agrégat pays (entityId NULL).
* ``REGIONAL`` — par région (entityId = regionId).

On limite volontairement à 2 échelons : le taux de transition par
préfecture/école est très bruité (faibles effectifs) et n'apporte pas
de valeur de pilotage IIPE — le cabinet veut un signal national +
régional fiable.
"""
from __future__ import annotations

from enum import StrEnum


class TransitionScope(StrEnum):
    """Granularité d'un ``TransitionRate``."""

    NATIONAL = "NATIONAL"
    REGIONAL = "REGIONAL"


__all__ = ["TransitionScope"]
