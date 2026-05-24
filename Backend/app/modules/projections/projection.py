"""Module 2B — Logique pure de projection des effectifs.

Aucun accès DB — fonctions pures testables sans fixture. La méthode
``project_one_year`` calcule les effectifs de l'année t+1 à partir de
ceux de t et des taux de transition par région.

Algorithme cohortes IIPE-UNESCO
-------------------------------

::

    projection[r, levelN, g, t+1] =
        enrollment[r, levelN-1, g, t]
        × transition_rate[r, levelN-1 → levelN, g]

Cas particulier MATERNELLE_1 (premier niveau, sans niveau précédent) :

::

    projection[r, MATERNELLE_1, g, t+1] =
        enrollment[r, MATERNELLE_1, g, t]
        × (1 + demographic_growth)

Stratégie de fallback (rate manquant)
-------------------------------------

1. Rate REGIONAL ``(region, level_from, gender)`` → utilisé.
2. Sinon rate NATIONAL ``(level_from, gender)`` → utilisé (toujours
   disponible si Module 2A a tourné).
3. Sinon → on garde le count précédent (signal data quality ; pas de
   division par zéro, pas de zéro silencieux).

Arrondi
-------

Les effectifs sont des entiers (élèves). ``round(half-even)`` pour
limiter les biais cumulés sur 5 ans.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from app.modules.enrollment.enums import EnrollmentClassLevel
from app.modules.projections.enums import (
    DEMOGRAPHIC_GROWTH_RATE_DEFAULT,
    TransitionScope,
)
from app.modules.projections.transitions import LEVEL_SEQUENCE
from app.shared.enums import Gender

# Clé d'agrégation : (regionId, classLevel, gender) → count.
EnrollmentCell = tuple[str, EnrollmentClassLevel, Gender]
EnrollmentMap = dict[EnrollmentCell, int]

# Clé d'un transition rate : (scope, entityId, levelFrom, gender).
# Pour scope=NATIONAL, entityId est conventionnellement ``None``.
TransitionRateKey = tuple[
    TransitionScope, str | None, EnrollmentClassLevel, Gender,
]
TransitionRateMap = dict[TransitionRateKey, Decimal]

# Index canonique des niveaux : pour MATERNELLE_1, level - 1 = -1 → growth démo.
_LEVEL_INDEX: dict[EnrollmentClassLevel, int] = {
    level: i for i, level in enumerate(LEVEL_SEQUENCE)
}


def _previous_level(
    level: EnrollmentClassLevel,
) -> EnrollmentClassLevel | None:
    """Renvoie le niveau précédent dans LEVEL_SEQUENCE, ou None pour MATERNELLE_1.

    Garde-fou : si le niveau n'est pas dans la séquence (cas inconnu), on
    renvoie None pour éviter une KeyError silencieuse plus loin.
    """
    idx = _LEVEL_INDEX.get(level)
    if idx is None or idx == 0:
        return None
    return LEVEL_SEQUENCE[idx - 1]


def _round_int(value: Decimal) -> int:
    """Arrondit un Decimal à l'entier (half-even, biais minimal)."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def _resolve_rate(
    rates: TransitionRateMap,
    *,
    region_id: str,
    level_from: EnrollmentClassLevel,
    gender: Gender,
) -> Decimal | None:
    """Cherche le rate régional, puis national, puis renvoie ``None``.

    None = "aucun rate connu" → l'appelant gardera le count précédent.
    """
    regional = rates.get(
        (TransitionScope.REGIONAL, region_id, level_from, gender),
    )
    if regional is not None:
        return regional
    national = rates.get(
        (TransitionScope.NATIONAL, None, level_from, gender),
    )
    return national


def project_one_year(
    prev_enrollments: EnrollmentMap,
    transition_rates: TransitionRateMap,
    demographic_growth: Decimal = DEMOGRAPHIC_GROWTH_RATE_DEFAULT,
) -> EnrollmentMap:
    """Projette les effectifs sur l'année suivante.

    Parameters
    ----------
    prev_enrollments : dict
        Effectifs de l'année t indexés par ``(region_id, level, gender)``.
    transition_rates : dict
        Rates indexés par ``(scope, entity_id, level_from, gender)``.
        Pour scope=NATIONAL, ``entity_id`` doit être ``None``.
    demographic_growth : Decimal
        Taux annuel pour MATERNELLE_1 (ex. 0.024 = +2.4 %).

    Returns
    -------
    dict
        Effectifs projetés ``(region_id, level, gender) → count``.

    Règles
    ------
    * MATERNELLE_1 → ``prev[r, MATERNELLE_1, g] × (1 + growth)``.
    * Autres niveaux → ``prev[r, level-1, g] × rate(r, level-1→level, g)``.
    * Rate manquant régional ET national → on garde ``prev[r, level, g]``
      (signal data quality, pas de zéro silencieux).
    * Tout résultat est arrondi à l'entier (half-even).
    """
    if demographic_growth < Decimal("-1"):
        raise ValueError(
            "demographic_growth doit être >= -1 (effectif négatif "
            f"interdit), reçu : {demographic_growth}",
        )

    out: EnrollmentMap = {}
    region_ids: set[str] = {key[0] for key in prev_enrollments}
    growth_multiplier = Decimal("1") + demographic_growth

    for region_id in region_ids:
        for level in LEVEL_SEQUENCE:
            for gender in (Gender.FEMALE, Gender.MALE):
                projected = _project_cell(
                    prev_enrollments=prev_enrollments,
                    transition_rates=transition_rates,
                    region_id=region_id,
                    level=level,
                    gender=gender,
                    growth_multiplier=growth_multiplier,
                )
                if projected is None:
                    continue
                out[(region_id, level, gender)] = projected
    return out


def _project_cell(
    *,
    prev_enrollments: EnrollmentMap,
    transition_rates: TransitionRateMap,
    region_id: str,
    level: EnrollmentClassLevel,
    gender: Gender,
    growth_multiplier: Decimal,
) -> int | None:
    """Projette une seule cellule (region, level, gender).

    Renvoie ``None`` si on n'a aucune donnée à projeter pour la cellule
    (pas de prev count même pour MATERNELLE_1, ni count level précédent).
    """
    prev_level = _previous_level(level)

    if prev_level is None:
        # MATERNELLE_1 : pas de niveau précédent → croissance démo.
        prev_count = prev_enrollments.get((region_id, level, gender))
        if prev_count is None:
            return None
        return _round_int(Decimal(prev_count) * growth_multiplier)

    # Niveau N : depuis le niveau N-1 année précédente.
    prev_count = prev_enrollments.get((region_id, prev_level, gender))
    if prev_count is None:
        return None
    rate = _resolve_rate(
        transition_rates,
        region_id=region_id,
        level_from=prev_level,
        gender=gender,
    )
    if rate is None:
        # Aucun rate connu (régional ni national) → on garde le count
        # précédent au même niveau (data quality signal).
        keep = prev_enrollments.get((region_id, level, gender))
        return keep if keep is not None else None
    return _round_int(Decimal(prev_count) * rate)


__all__ = [
    "EnrollmentCell",
    "EnrollmentMap",
    "TransitionRateKey",
    "TransitionRateMap",
    "project_one_year",
]
