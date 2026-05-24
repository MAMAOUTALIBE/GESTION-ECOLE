"""Module 3C — Logique pure de scoring (sans DB).

Toutes les fonctions sont déterministes, prennent des structures simples
(dict, primitives) et retournent ``(score_pondéré, breakdown_details)``.

Objectifs :

* Testable unitairement sans fixture DB.
* Facile à raisonner : chaque dimension est isolée, les seuils sont
  explicites en tête de chaque fonction.
* Les détails du breakdown servent à la UI (popup d'audit cabinet) et à
  la traçabilité.

Conventions
-----------
* Scores en INT (0..n). On évite Decimal — on ne fait que des additions
  pondérées d'entiers.
* Pondérations appliquées à l'intérieur des fonctions (les seuils sont
  exprimés directement en points pondérés, cf. spec Module 3C).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.modules.investment.enums import (
    THRESHOLD_HAUTE,
    THRESHOLD_MOYENNE,
    THRESHOLD_TRES_HAUTE,
    PriorityCategory,
)
from app.modules.projections.enums import CapacitySeverity
from app.shared.enums import BuildingCondition, ZoneType


def score_infrastructure(school_data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Score infrastructure (0..35, pondération directe).

    Points attribués (somme = 35 max) :

    * +10 si pas de source d'eau ou ``waterSource`` IN (NULL, 'NONE').
    * +10 si pas d'électricité ou ``electricitySource`` IN (NULL, 'NONE').
    * +10 si aucune toilette (toiletsBoys = 0 ET toiletsGirls = 0).
    * État bâtiment (max 15 pts) :
        - DANGEROUS / POOR : +15
        - FAIR             : +10
        - GOOD             : +5
        - EXCELLENT        : 0
        - NULL             : +10 (donnée manquante = signal d'alerte
          modéré, cabinet doit faire inspecter).

      NB la spec mentionne "BAD" : on mappe sur DANGEROUS (le code Prisma
      utilise DANGEROUS pour le pire état).
    * +15 si ratio ``classroomsUsable/classroomsTotal`` < 0.5 (école aux
      salles majoritairement inutilisables — urgence réhabilitation).
    * +5 si ``internetAvailable`` est False.

    Total max théorique : 65 pts. On le **plafonne à 35** pour respecter
    la pondération de la dimension (sinon la dimension peut écraser les
    autres).
    """
    points = 0
    details: dict[str, Any] = {}

    water = school_data.get("waterSource")
    missing_water = water is None or str(water) == "NONE" or (
        hasattr(water, "value") and water.value == "NONE"
    )
    if missing_water:
        points += 10
    details["missingWater"] = missing_water

    electricity = school_data.get("electricitySource")
    missing_electricity = electricity is None or str(electricity) == "NONE" or (
        hasattr(electricity, "value") and electricity.value == "NONE"
    )
    if missing_electricity:
        points += 10
    details["missingElectricity"] = missing_electricity

    toilets_b = school_data.get("toiletsBoys") or 0
    toilets_g = school_data.get("toiletsGirls") or 0
    missing_toilets = (int(toilets_b) == 0) and (int(toilets_g) == 0)
    if missing_toilets:
        points += 10
    details["missingToilets"] = missing_toilets

    building = school_data.get("buildingCondition")
    building_value = (
        building.value if hasattr(building, "value")
        else (str(building) if building is not None else None)
    )
    building_points: int
    if building_value in (
        BuildingCondition.DANGEROUS.value,
        BuildingCondition.POOR.value,
    ):
        building_points = 15
    elif building_value == BuildingCondition.FAIR.value:
        building_points = 10
    elif building_value == BuildingCondition.GOOD.value:
        building_points = 5
    elif building_value == BuildingCondition.EXCELLENT.value:
        building_points = 0
    else:
        # Donnée manquante : signal modéré.
        building_points = 10
    points += building_points
    details["buildingCondition"] = building_value
    details["buildingPoints"] = building_points

    total = school_data.get("classroomsTotal") or 0
    usable = school_data.get("classroomsUsable") or 0
    ratio: float | None = None
    if int(total) > 0:
        ratio = float(usable) / float(total)
    classroom_ratio_critical = ratio is not None and ratio < 0.5
    if classroom_ratio_critical:
        points += 15
    details["classroomsRatio"] = ratio
    details["classroomsRatioCritical"] = classroom_ratio_critical

    internet = bool(school_data.get("internetAvailable") or False)
    missing_internet = not internet
    if missing_internet:
        points += 5
    details["missingInternet"] = missing_internet

    # Plafonnage à la pondération de la dimension (35).
    capped = min(points, 35)
    details["rawPoints"] = points
    details["score"] = capped
    return capped, details


def score_saturation(
    severity: CapacitySeverity | None,
) -> tuple[int, dict[str, Any]]:
    """Score saturation projetée (0..25, pondération directe).

    Mapping :

    * CRITICAL → 25 (sur-capacité projetée à t+1 — investir d'urgence).
    * WARNING  → 15 (proche du seuil — anticiper).
    * OK       → 0
    * NULL     → 0 (pas de snapshot — on ne pénalise pas l'école pour
      l'absence de données projetées).
    """
    if severity == CapacitySeverity.CRITICAL:
        score = 25
    elif severity == CapacitySeverity.WARNING:
        score = 15
    else:
        score = 0
    severity_value = (
        severity.value if isinstance(severity, CapacitySeverity)
        else (severity if severity is not None else None)
    )
    return score, {"severity": severity_value, "score": score}


def score_equity(gpi: Decimal | float | None) -> tuple[int, dict[str, Any]]:
    """Score équité (0..25, pondération directe).

    Le GPI = filles / garçons. Seuils standards :

    * CRITICAL si GPI < 0.85           → 25 pts (forte disparité filles).
    * WARNING  si 0.85 <= GPI < 0.97   → 15 pts.
    * NORMAL   sinon                   → 0 pt.
    * NULL     (école sans Enrollment) → 0 pt.

    On parle ici du GPI école : un GPI > 1 (favorable filles) ne pénalise
    pas l'école (la priorité concerne la sous-scolarisation des filles).
    """
    if gpi is None:
        return 0, {"gpi": None, "severity": "UNKNOWN", "score": 0}
    gpi_decimal = (
        gpi if isinstance(gpi, Decimal) else Decimal(str(gpi))
    )
    severity_label: str
    if gpi_decimal < Decimal("0.85"):
        score = 25
        severity_label = "CRITICAL"
    elif gpi_decimal < Decimal("0.97"):
        score = 15
        severity_label = "WARNING"
    else:
        score = 0
        severity_label = "NORMAL"
    return score, {
        "gpi": float(gpi_decimal),
        "severity": severity_label,
        "score": score,
    }


def score_accessibility(
    zone_type: ZoneType,
    avg_distance_km: float | None = None,
) -> tuple[int, dict[str, Any]]:
    """Score accessibilité (0..20 ; pondéré à 15 + bonus distance 5).

    * RURAL      → 15 pts (zone la plus mal desservie historiquement).
    * PERI_URBAN → 8 pts.
    * URBAN      → 0 pt.
    * Bonus +5 si ``avg_distance_km`` > 3 km (école-élève moyen >
      seuil de proximité IIPE).

    Note : la spec parle de 15 % pour la dimension ; le bonus distance
    permet d'aller jusqu'à 20 pts pour les écoles à la fois rurales et
    isolées (situations les plus précaires).
    """
    if zone_type == ZoneType.RURAL:
        zone_score = 15
    elif zone_type == ZoneType.PERI_URBAN:
        zone_score = 8
    else:
        zone_score = 0
    distance_bonus = 0
    if avg_distance_km is not None and avg_distance_km > 3.0:
        distance_bonus = 5
    return zone_score + distance_bonus, {
        "zoneType": zone_type.value if hasattr(zone_type, "value") else str(zone_type),
        "zonePoints": zone_score,
        "avgDistanceKm": avg_distance_km,
        "distanceBonus": distance_bonus,
        "score": zone_score + distance_bonus,
    }


def compute_total(scores: list[int]) -> int:
    """Total = somme des scores partiels. Plafonné à 100 pour rester sur
    l'échelle 0-100 attendue côté UI."""
    return min(sum(scores), 100)


def classify(total: int) -> PriorityCategory:
    """Classification d'un score total en catégorie de priorité.

    Bornes inclusives sur la borne basse (cf. ``enums.py``).
    """
    if total >= THRESHOLD_TRES_HAUTE:
        return PriorityCategory.TRES_HAUTE
    if total >= THRESHOLD_HAUTE:
        return PriorityCategory.HAUTE
    if total >= THRESHOLD_MOYENNE:
        return PriorityCategory.MOYENNE
    return PriorityCategory.BASSE


__all__ = [
    "classify",
    "compute_total",
    "score_accessibility",
    "score_equity",
    "score_infrastructure",
    "score_saturation",
]
