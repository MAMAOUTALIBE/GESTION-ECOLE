"""Module 19 — Enums dédiés au cockpit ministériel.

* ``KpiKey``       — clés normalisées pour les KPI snapshots (table
  CockpitKpiSnapshot). Permet de filtrer rapidement les séries
  historiques d'un seul KPI sans introspection JSON.
* ``CockpitScope`` — portée d'un snapshot (national ou régional).
* ``AlertSeverity`` — alias local pour la sévérité d'une "top alerte"
  côté cockpit (on évite de réimporter AnomalySeverity dans tous les
  callers ; les valeurs sont alignées).
"""
from __future__ import annotations

from enum import StrEnum


class KpiKey(StrEnum):
    """Clés normalisées des KPI nationaux trackés en snapshot.

    Convention : SNAKE_CASE majuscule. Ajouter une clé ici doit s'accompagner
    d'une logique de calcul dans ``CockpitService._compute_kpi`` (sinon le
    snapshot quotidien ne saura pas la calculer).
    """

    STUDENTS_TOTAL = "STUDENTS_TOTAL"
    ATTENDANCE_RATE = "ATTENDANCE_RATE"
    BUDGET_CONSUMPTION = "BUDGET_CONSUMPTION"
    CRITICAL_ANOMALIES_OPEN = "CRITICAL_ANOMALIES_OPEN"
    ALERTS_OPEN = "ALERTS_OPEN"
    # Module 1B — GPI national courant (filles / garçons agrégé sur
    # toutes les écoles ayant déclaré pour la dernière année active).
    NATIONAL_GPI = "NATIONAL_GPI"
    # Module 2C — Nombre d'écoles en CRITICAL sur la projection à horizon
    # t+1 (saturation > 100 %). Indicateur clef pour le pilotage des
    # investissements infrastructure.
    PROJECTED_CRITICAL_SCHOOLS_COUNT = "PROJECTED_CRITICAL_SCHOOLS_COUNT"
    # Module 2D — Nombre d'écoles en CRITICAL staffing (ratio
    # élèves/enseignant > 70). Indicateur clef pour la répartition des
    # enseignants (objectif gouv. "optimiser la répartition des
    # enseignants").
    SCHOOLS_CRITICAL_STAFFING_COUNT = "SCHOOLS_CRITICAL_STAFFING_COUNT"


class CockpitScope(StrEnum):
    """Portée d'un KPI snapshot : agrégat national ou régional."""

    NATIONAL = "NATIONAL"
    REGIONAL = "REGIONAL"


class AlertSeverity(StrEnum):
    """Sévérité d'une alerte cockpit (alias local d'AnomalySeverity).

    On garde l'enum local pour découpler le contrat API cockpit du
    module anomalies (si demain une alerte vient d'un autre détecteur,
    on n'a pas à le faire transiter via l'enum anomalies).
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


__all__ = ["AlertSeverity", "CockpitScope", "KpiKey"]
