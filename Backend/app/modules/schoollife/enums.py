"""Enums spécifiques au module schoollife (Module 7).

Les enums historiques (IncidentType, HealthVisitType, …) restent dans
``app.shared.enums`` pour compatibilité avec le code existant. On en ajoute
ici quelques-uns greenfield introduits par Module 7 (vaccinations,
allergies, cantines avec attendance, abonnements bus).
"""
from __future__ import annotations

from enum import StrEnum

# Re-export pour utilisation locale (un seul import depuis le module)
from app.shared.enums import (  # noqa: F401
    DayOfWeek,
    HealthVisitStatus,
    HealthVisitType,
    IncidentSanction,
    IncidentSeverity,
    IncidentType,
    MealServiceType,
    TransportRouteStatus,
)


class AllergySeverity(StrEnum):
    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"
    ANAPHYLACTIC = "ANAPHYLACTIC"


class AllergyCategory(StrEnum):
    FOOD = "FOOD"
    DRUG = "DRUG"
    ENVIRONMENTAL = "ENVIRONMENTAL"
    OTHER = "OTHER"


class VaccinationStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    ADMINISTERED = "ADMINISTERED"
    SKIPPED = "SKIPPED"
    REFUSED = "REFUSED"


class MealAttendanceStatus(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    EXCUSED = "EXCUSED"


class BusSubscriptionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class IncidentStatus(StrEnum):
    """Statut administratif d'un incident (ouvert → traité)."""

    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
