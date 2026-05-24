"""Module 9 — Anomalies detection : enums dédiés.

Trois enums :
* ``AnomalyType`` — type métier de l'anomalie (correspond 1:1 à un détecteur).
* ``AnomalySeverity`` — gravité (LOW..CRITICAL) pour prioriser le triage.
* ``AnomalyStatus`` — workflow human-in-the-loop : PENDING → CONFIRMED |
  DISMISSED | FALSE_POSITIVE (la révision est faite par un directeur).
"""
from enum import StrEnum


class AnomalyType(StrEnum):
    """Famille de détecteur ayant produit l'anomalie.

    Ajouter un nouveau type ici DOIT s'accompagner :
    * d'un détecteur dans ``app.modules.anomalies.detectors``,
    * d'une entrée dans la migration alembic (l'enum est native_enum côté
      Postgres mais on stocke le texte tel quel pour rester portable).
    """

    IMPOSSIBLE_GRADE = "IMPOSSIBLE_GRADE"
    SUSPICIOUS_ATTENDANCE = "SUSPICIOUS_ATTENDANCE"
    GRADE_JUMP = "GRADE_JUMP"
    INVALID_BIRTHDATE = "INVALID_BIRTHDATE"
    DUPLICATE_CODE = "DUPLICATE_CODE"
    EXCESSIVE_TRANSFER = "EXCESSIVE_TRANSFER"
    # Module 1B — point chaud GPI : GPI < 0.85 → cible "améliorer la
    # scolarisation des filles". Détecteur déclenché par
    # ``EnrollmentService.compute_gpi_snapshots`` (pas un détecteur SQL
    # générique : il est dépendant d'un schoolYearId explicite).
    CRITICAL_GPI = "CRITICAL_GPI"
    # Module 1C — écart de GPI urbain/rural > 0.10 sur une région.
    # Objectif gouv : corriger les disparités urbain vs rural. Détecteur
    # déclenché manuellement ou via un job CRON après chaque recalcul GPI.
    URBAN_RURAL_GPI_GAP = "URBAN_RURAL_GPI_GAP"
    # Module 2A — taux de transition aberrant (rate > 2 ou rate < 0.5).
    # Signal de redoublement de masse, erreur de saisie, ou abandon
    # massif entre deux niveaux scolaires sur une région. Détecteur
    # déclenché en hook post-``compute_transitions``.
    TRANSITION_RATE_OUTLIER = "TRANSITION_RATE_OUTLIER"


class AnomalySeverity(StrEnum):
    """Gravité de l'anomalie.

    Convention :
    * LOW : audit (souvent un FALSE_POSITIVE attendu, ex : transferts répétés
      mais documentés).
    * MEDIUM : à investiguer dans la semaine.
    * HIGH : à investiguer dans la journée (fraude potentielle).
    * CRITICAL : blocage métier (note > 20, birthDate impossible).
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AnomalyStatus(StrEnum):
    """Workflow de revue d'une anomalie.

    PENDING → CONFIRMED         : l'anomalie est réelle, action métier requise.
    PENDING → DISMISSED         : l'anomalie est réelle mais acceptable.
    PENDING → FALSE_POSITIVE    : le détecteur s'est trompé (utile pour
                                  retoucher le seuil ou désactiver le détecteur).
    """

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
