"""Module 2C — Logique pure de capacité / demande projetée.

Aucun accès DB. Trois fonctions :

* ``compute_school_capacity`` : nb de places disponibles à partir des
  salles utilisables et de la norme MEN.
* ``compute_saturation_pct`` : ratio demande / capacité × 100. Renvoie
  ``None`` si capacity == 0 (école sans capacité utilisable).
* ``compute_severity`` : classification OK / WARNING / CRITICAL à
  partir d'une saturation.

Utilisé par ``CapacityDemandService`` et directement testable sans
fixture DB.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from app.modules.projections.enums import (
    CRITICAL_THRESHOLD,
    STUDENTS_PER_CLASSROOM_NORM,
    WARNING_THRESHOLD,
    CapacitySeverity,
)


def compute_school_capacity(
    classrooms_usable: int,
    norm: int = STUDENTS_PER_CLASSROOM_NORM,
) -> int:
    """Capacité physique d'une école = salles utilisables × norme MEN.

    Parameters
    ----------
    classrooms_usable : int
        Nombre de salles de classe utilisables. Valeur négative interdite
        (signal de saisie aberrante → on remonte ValueError plutôt que
        de produire silencieusement une capacité négative).
    norm : int
        Élèves max par salle (par défaut norme MEN Guinée = 50).

    Returns
    -------
    int
        Capacité totale ; ``0`` si ``classrooms_usable == 0``.
    """
    if classrooms_usable < 0:
        raise ValueError(
            "classrooms_usable doit être >= 0, "
            f"reçu : {classrooms_usable}",
        )
    if norm <= 0:
        raise ValueError(
            f"norm doit être > 0, reçu : {norm}",
        )
    return classrooms_usable * norm


def compute_saturation_pct(
    demand: int,
    capacity: int,
) -> Decimal | None:
    """Pourcentage de saturation = demand / capacity × 100.

    Renvoie ``None`` si ``capacity <= 0`` (école sans capacité utilisable
    — on évite la division par zéro et on signale la donnée comme à
    compléter côté service).

    Précision : NUMERIC(6,2) (cohérent avec la colonne DB).
    """
    if capacity <= 0:
        return None
    raw = (Decimal(demand) / Decimal(capacity)) * Decimal("100")
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def compute_severity(
    saturation_pct: Decimal | None,
) -> CapacitySeverity:
    """Classification du niveau d'alerte selon le pourcentage de saturation.

    * ``None`` (capacity = 0) → ``CRITICAL`` : aucune capacité utilisable
      mais une demande projetée → l'école doit être construite ou
      réhabilitée.
    * ``saturation <= 80`` → ``OK``.
    * ``80 < saturation <= 100`` → ``WARNING``.
    * ``saturation > 100`` → ``CRITICAL``.
    """
    if saturation_pct is None:
        # Pas de capacité utilisable mais une demande existe → alarme
        # maximale ; l'école doit être (re)construite.
        return CapacitySeverity.CRITICAL
    if saturation_pct <= WARNING_THRESHOLD:
        return CapacitySeverity.OK
    if saturation_pct <= CRITICAL_THRESHOLD:
        return CapacitySeverity.WARNING
    return CapacitySeverity.CRITICAL


def compute_gap(demand: int, capacity: int) -> int:
    """Écart entre demande projetée et capacité = ``demand - capacity``.

    Renvoie un entier (positif = sur-capacité, négatif = marge).
    """
    return demand - capacity


__all__ = [
    "compute_gap",
    "compute_saturation_pct",
    "compute_school_capacity",
    "compute_severity",
]
