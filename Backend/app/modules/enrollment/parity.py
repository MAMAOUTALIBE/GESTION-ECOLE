"""Module 1B — Calcul du Gender Parity Index (GPI) + classification UNESCO.

Constantes (seuils UNESCO/IIPE)
-------------------------------
* **NORMAL**          : 0.97 ≤ GPI ≤ 1.03 — parité acceptable.
* **WARNING_GIRLS**   : 0.85 ≤ GPI < 0.97 — disparité au détriment des filles.
* **CRITICAL_GIRLS**  : GPI < 0.85 — point chaud gouvernemental ("améliorer
  la scolarisation des filles"). Déclenche une anomalie Module 9.
* **WARNING_BOYS**    : GPI > 1.03 — disparité au détriment des garçons.

Convention "boys = 0"
---------------------
Diviser par zéro n'a aucun sens métier ici. On distingue deux cas :

* ``girls == 0 and boys == 0`` → ``None`` (pas de cohorte mesurable).
  La sévérité retourne alors ``NORMAL`` (par défaut, on ne signale rien sur
  une école qui n'a tout simplement pas inscrit dans ce scope).
* ``boys == 0 and girls > 0`` → on renvoie une constante symbolique
  ``MALE_ABSENT_GPI = Decimal("999.9999")`` pour matérialiser une absence
  totale de garçons (cas extrême : cohorte 100% filles). Cette valeur est
  classée ``CRITICAL_GIRLS`` côté ``classify_gpi`` car elle indique une
  disparité massive (et est volontairement plate plutôt que infinie pour
  rester stockable en ``NUMERIC(6,4)``).

Decimal vs float
----------------
Les chiffres GPI remontent au cabinet (rapports gouv). On utilise
``Decimal`` partout (jamais ``float``) pour garantir la reproductibilité
à 4 décimales — précision identique à la définition stricte UNESCO.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum

# Précision NUMERIC(6,4) — 4 décimales fixées.
_GPI_QUANTUM = Decimal("0.0001")

# Constantes seuils (Decimal pour cohérence avec le calcul).
GPI_NORMAL_MIN = Decimal("0.97")
GPI_NORMAL_MAX = Decimal("1.03")
GPI_CRITICAL_GIRLS_MAX = Decimal("0.85")  # GPI < 0.85 → CRITICAL_GIRLS

# Sentinelle "aucun garçon" — utilisée quand boys == 0 mais girls > 0. La
# valeur est délibérément finie pour rester stockable en NUMERIC(6,4) et
# rester comparable. Classée CRITICAL_GIRLS par classify_gpi.
MALE_ABSENT_GPI = Decimal("999.9999")

# Dictionnaire exposé pour faciliter la doc auto-générée côté frontend.
GPI_THRESHOLDS: dict[str, Decimal] = {
    "NORMAL_MIN": GPI_NORMAL_MIN,
    "NORMAL_MAX": GPI_NORMAL_MAX,
    "CRITICAL_GIRLS_MAX": GPI_CRITICAL_GIRLS_MAX,
    "MALE_ABSENT_GPI": MALE_ABSENT_GPI,
}


class GpiSeverity(StrEnum):
    """Classification UNESCO d'un GPI donné.

    L'ordre est volontaire pour faciliter le tri ascendant (NORMAL en
    premier, CRITICAL_GIRLS en dernier dans une liste triée par sévérité).
    """

    NORMAL = "NORMAL"
    WARNING_GIRLS = "WARNING_GIRLS"
    CRITICAL_GIRLS = "CRITICAL_GIRLS"
    WARNING_BOYS = "WARNING_BOYS"


def compute_gpi(girls: int, boys: int) -> Decimal | None:
    """Calcule le Gender Parity Index = filles / garçons.

    Retourne :
    * ``None`` si girls == 0 ET boys == 0 (rien à mesurer).
    * ``MALE_ABSENT_GPI`` (Decimal symbolique) si boys == 0 mais girls > 0
      (cohorte 100% filles, déclencheur "alerte parité absolue").
    * Sinon un ``Decimal`` à 4 décimales (arrondi banker's, conformément
      à la convention IIPE).
    """
    if girls < 0 or boys < 0:
        raise ValueError(
            "compute_gpi: les effectifs doivent être ≥ 0 "
            f"(girls={girls}, boys={boys})."
        )
    if girls == 0 and boys == 0:
        return None
    if boys == 0:
        return MALE_ABSENT_GPI
    raw = Decimal(girls) / Decimal(boys)
    return raw.quantize(_GPI_QUANTUM, rounding=ROUND_HALF_EVEN)


def classify_gpi(gpi: Decimal | None) -> GpiSeverity:
    """Classe un GPI selon les seuils UNESCO.

    * ``None``                   → ``NORMAL`` (rien à signaler sur une
      cohorte vide — ce n'est pas une anomalie, c'est une absence de data).
    * ``MALE_ABSENT_GPI``        → ``CRITICAL_GIRLS`` (parité totalement
      absente d'un côté).
    * 0.97 ≤ gpi ≤ 1.03          → ``NORMAL``.
    * 0.85 ≤ gpi < 0.97          → ``WARNING_GIRLS``.
    * gpi < 0.85                 → ``CRITICAL_GIRLS``.
    * gpi > 1.03 (hors sentinelle) → ``WARNING_BOYS``.
    """
    if gpi is None:
        return GpiSeverity.NORMAL
    if gpi == MALE_ABSENT_GPI:
        # Cohorte 100% filles : alerte forte côté équité (paradoxalement
        # même sens que CRITICAL_GIRLS — ce n'est pas une parité saine).
        return GpiSeverity.CRITICAL_GIRLS
    if GPI_NORMAL_MIN <= gpi <= GPI_NORMAL_MAX:
        return GpiSeverity.NORMAL
    if gpi < GPI_CRITICAL_GIRLS_MAX:
        return GpiSeverity.CRITICAL_GIRLS
    if gpi < GPI_NORMAL_MIN:
        return GpiSeverity.WARNING_GIRLS
    # gpi > GPI_NORMAL_MAX (strict)
    return GpiSeverity.WARNING_BOYS


__all__ = [
    "GPI_CRITICAL_GIRLS_MAX",
    "GPI_NORMAL_MAX",
    "GPI_NORMAL_MIN",
    "GPI_THRESHOLDS",
    "MALE_ABSENT_GPI",
    "GpiSeverity",
    "classify_gpi",
    "compute_gpi",
]
