"""Module 2D — Logique pure pour le staffing enseignants.

Ce module n'a aucune dépendance SQLAlchemy : il expose des fonctions
pures qui peuvent être testées sans DB. C'est volontaire — l'algorithme
de classification + scoring doit rester auditable et reproductible.

Conventions
-----------
* Tous les ratios sont retournés en ``Decimal`` (jamais float) pour
  préserver la précision rapport IIPE.
* ``compute_ratio`` retourne ``None`` si ``teachers <= 0`` — pas de
  division par zéro silencieuse.
* ``classify_staffing(None)`` est traité comme CRITICAL : une école sans
  enseignant est un signal grave qui doit remonter.
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

from app.modules.projections.enums import (
    CRITICAL_RATIO,
    OVER_STAFFED_RATIO,
    STUDENTS_PER_TEACHER_NORM,
    UNDER_STAFFED_RATIO,
    StaffingSeverity,
)


def compute_ratio(students: int, teachers: int) -> Decimal | None:
    """Calcule le ratio élèves / enseignant.

    Retourne ``None`` si ``teachers <= 0`` (école sans enseignant — pas
    de division par zéro silencieuse). Sinon ``Decimal`` arrondi à 2
    décimales (HALF_UP).
    """
    if teachers <= 0:
        return None
    if students < 0:
        raise ValueError(
            "students doit être ≥ 0, valeur reçue : "
            f"{students}"
        )
    ratio = Decimal(students) / Decimal(teachers)
    return ratio.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def classify_staffing(ratio: Decimal | None) -> StaffingSeverity:
    """Classe une école selon son ratio élèves / enseignant.

    Table des seuils (cf. enums.py) :

    * ratio < 25         → OVER_STAFFED
    * 25 ≤ ratio ≤ 50    → ADEQUATE
    * 50 < ratio ≤ 70    → UNDER_STAFFED
    * ratio > 70         → CRITICAL
    * ratio None         → CRITICAL (école sans enseignant)
    """
    if ratio is None:
        return StaffingSeverity.CRITICAL
    if ratio < OVER_STAFFED_RATIO:
        return StaffingSeverity.OVER_STAFFED
    if ratio <= UNDER_STAFFED_RATIO:
        return StaffingSeverity.ADEQUATE
    if ratio <= CRITICAL_RATIO:
        return StaffingSeverity.UNDER_STAFFED
    return StaffingSeverity.CRITICAL


def expected_teachers(
    students: int, norm: int = STUDENTS_PER_TEACHER_NORM,
) -> int:
    """Nombre d'enseignants attendus pour ``students`` élèves selon la norme.

    Formule : ``ceil(students / norm)``. La norme par défaut est celle du
    MEN Guinée (40). On utilise ``ceil`` car le besoin d'enseignants ne
    se fractionne pas — 41 élèves nécessitent 2 enseignants même si le
    ratio mathématique est 1.025.

    ``norm`` doit être > 0 (raise ValueError sinon).
    ``students`` < 0 lève ValueError (donnée invalide).
    """
    if norm <= 0:
        raise ValueError(f"norm doit être > 0, valeur reçue : {norm}")
    if students < 0:
        raise ValueError(
            f"students doit être ≥ 0, valeur reçue : {students}"
        )
    if students == 0:
        return 0
    return math.ceil(students / norm)


def compute_gap(actual_teachers: int, expected: int) -> int:
    """Calcule l'écart enseignants attendus - enseignants réels.

    * Négatif → sur-doté (trop d'enseignants pour le besoin).
    * Zéro    → adéquat.
    * Positif → sous-doté (manque d'enseignants).
    """
    return expected - actual_teachers


def compute_priority_score(
    donor_ratio: Decimal | None,
    receiver_ratio: Decimal | None,
    same_prefecture: bool,
) -> Decimal:
    """Score d'urgence d'une recommandation de transfert.

    Heuristique simple et auditable :

    * Plus le ratio du receveur est élevé → plus le score est élevé
      (école sur-saturée prioritaire).
    * Plus le ratio du donneur est faible → plus le score est élevé
      (école sur-dotée peut céder facilement).
    * Bonus de +20 si la paire est dans la même préfecture (préférer
      les transferts internes — moins de mobilité = moins de friction).

    Formule :
        score = receiver_ratio - donor_ratio (+ 20 si same_prefecture)

    Score retourné en ``Decimal(6,2)``. Les ratios ``None`` sont
    interprétés comme 0 (pas d'info → score neutre côté donneur ; CRITICAL
    côté receveur).
    """
    donor = donor_ratio if donor_ratio is not None else Decimal("0")
    receiver = (
        receiver_ratio if receiver_ratio is not None else Decimal("0")
    )
    base = receiver - donor
    if same_prefecture:
        base = base + Decimal("20")
    return base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


__all__ = [
    "classify_staffing",
    "compute_gap",
    "compute_priority_score",
    "compute_ratio",
    "expected_teachers",
]
