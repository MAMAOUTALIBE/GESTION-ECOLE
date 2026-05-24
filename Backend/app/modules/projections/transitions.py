"""Module 2A — Logique métier pure du calcul de transition.

Aucun accès DB ici — fonctions pures, testables sans fixture.

Formule IIPE-UNESCO
-------------------
::

    tt(region, levelN→levelN+1, gender, year_t) =
       enrollment[region, levelN+1, gender, year_t+1]
       /
       enrollment[region, levelN, gender, year_t]

Garde-fous (outlier)
--------------------
* ``count_from = 0`` → rate = ``None`` (pas de division par zéro,
  pas d'outlier).
* ``rate > 2`` → ``is_outlier = True`` (redoublement de masse / erreur).
* ``rate < 0`` → ``is_outlier = True`` (impossible mais blindé).

Précision
---------
``Decimal`` à 4 décimales (NUMERIC(6,4)) — convention IIPE.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from app.modules.enrollment.enums import EnrollmentClassLevel

# Seuils outlier (Decimal — cohérent avec le calcul).
OUTLIER_THRESHOLD_HIGH = Decimal("2.0")
OUTLIER_THRESHOLD_LOW = Decimal("0.0")

# Précision NUMERIC(6,4) — identique à GPI Module 1B.
_RATE_QUANTUM = Decimal("0.0001")


# Séquence canonique des niveaux primaires guinéens (ordre IIPE).
# Maternelle d'abord (préscolaire), puis CP1..CM2.
LEVEL_SEQUENCE: list[EnrollmentClassLevel] = [
    EnrollmentClassLevel.MATERNELLE_1,
    EnrollmentClassLevel.MATERNELLE_2,
    EnrollmentClassLevel.MATERNELLE_3,
    EnrollmentClassLevel.CP1,
    EnrollmentClassLevel.CP2,
    EnrollmentClassLevel.CE1,
    EnrollmentClassLevel.CE2,
    EnrollmentClassLevel.CM1,
    EnrollmentClassLevel.CM2,
]


# Paires de transition consécutives. CM2 → fin de cycle primaire (pas de
# transition intra-primaire : c'est l'entrée au secondaire qui est mesurée
# par un autre indicateur, hors périmètre Module 2A).
LEVEL_PAIRS: list[tuple[EnrollmentClassLevel, EnrollmentClassLevel]] = [
    (LEVEL_SEQUENCE[i], LEVEL_SEQUENCE[i + 1])
    for i in range(len(LEVEL_SEQUENCE) - 1)
]


def compute_rate(
    count_from: int, count_to: int,
) -> tuple[Decimal | None, bool]:
    """Calcule le taux de transition (count_to / count_from).

    Retourne ``(rate, is_outlier)`` :

    * ``rate = None`` si ``count_from = 0`` (pas de cohorte source).
      ``is_outlier = False`` (rien à signaler — c'est une absence de data).
    * Sinon ``rate = Decimal(count_to / count_from)`` à 4 décimales.
    * ``is_outlier = True`` si ``rate > 2`` (redoublement / erreur) ou
      ``rate < 0`` (impossible mais blindé).
    """
    if count_from < 0 or count_to < 0:
        raise ValueError(
            "compute_rate: les effectifs doivent être ≥ 0 "
            f"(count_from={count_from}, count_to={count_to})."
        )
    if count_from == 0:
        return None, False
    raw = Decimal(count_to) / Decimal(count_from)
    rate = raw.quantize(_RATE_QUANTUM, rounding=ROUND_HALF_EVEN)
    is_outlier = rate > OUTLIER_THRESHOLD_HIGH or rate < OUTLIER_THRESHOLD_LOW
    return rate, is_outlier


__all__ = [
    "LEVEL_PAIRS",
    "LEVEL_SEQUENCE",
    "OUTLIER_THRESHOLD_HIGH",
    "OUTLIER_THRESHOLD_LOW",
    "compute_rate",
]
