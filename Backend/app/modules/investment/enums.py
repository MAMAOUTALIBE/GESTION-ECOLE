"""Module 3C — Enums et constantes du score d'investissement.

PriorityCategory
----------------
Catégorisation finale (en français pour rester aligné avec l'UX
ministérielle Guinée) :

* ``TRES_HAUTE`` — score >= 70 : action immédiate, priorité absolue.
* ``HAUTE``     — 50 <= score < 70 : à intégrer au plan triennal.
* ``MOYENNE``   — 30 <= score < 50 : à surveiller, action différée.
* ``BASSE``     — score < 30 : pas d'investissement à court terme.

Pondérations
------------
Total 100 % réparti pour refléter la doctrine IIPE-UNESCO :

* Infrastructure (35 %) — levier le plus actionnable, données solides.
* Saturation projetée (25 %) — anticipation démographique.
* Équité (25 %) — objectif gouv. de réduction des disparités filles.
* Accessibilité (15 %) — moindre poids car partiellement compensable
  par transport scolaire (Module 7).

Seuils
------
Bornes choisies pour qu'environ 10-20 % des écoles soient TRES_HAUTE
et 25-30 % HAUTE (volume gérable budgétairement). Mutables si la
distribution réelle s'écarte trop.
"""
from __future__ import annotations

from enum import StrEnum


class PriorityCategory(StrEnum):
    """Catégorie finale de priorité d'investissement d'une école."""

    TRES_HAUTE = "TRES_HAUTE"
    HAUTE = "HAUTE"
    MOYENNE = "MOYENNE"
    BASSE = "BASSE"


# Pondérations des 4 dimensions (somme = 100). Servent à dimensionner les
# scores partiels retournés par les fonctions de scoring.
WEIGHT_INFRASTRUCTURE: int = 35
WEIGHT_SATURATION: int = 25
WEIGHT_EQUITY: int = 25
WEIGHT_ACCESSIBILITY: int = 15

# Seuils de classification (inclusif sur la borne basse).
THRESHOLD_TRES_HAUTE: int = 70
THRESHOLD_HAUTE: int = 50
THRESHOLD_MOYENNE: int = 30


__all__ = [
    "THRESHOLD_HAUTE",
    "THRESHOLD_MOYENNE",
    "THRESHOLD_TRES_HAUTE",
    "WEIGHT_ACCESSIBILITY",
    "WEIGHT_EQUITY",
    "WEIGHT_INFRASTRUCTURE",
    "WEIGHT_SATURATION",
    "PriorityCategory",
]
