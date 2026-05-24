"""Module schoollife (Module 7) — 4 sous-domaines :
* Discipline (Incident)
* Santé (HealthVisit, Vaccination, StudentAllergy)
* Cantines (MealService, MealMenu, MealAttendance)
* Transport (BusRoute, BusStop, StudentBusSubscription)

Plus l'héritage Phase 13 (TimetableSlot).
"""
from app.modules.schoollife import enums, models, schemas, service
from app.modules.schoollife.router import router as legacy_router
from app.modules.schoollife.routers import (
    discipline_router,
    health_router,
    meals_router,
    transport_router,
)
from app.modules.schoollife.service import (
    DisciplineService,
    DiscplineService,
    HealthService,
    MealServiceModule,
    MealsService,
    SchoolLifeService,
    TransportService,
)

__all__ = [
    "DisciplineService",
    "DiscplineService",
    "HealthService",
    "MealServiceModule",
    "MealsService",
    "SchoolLifeService",
    "TransportService",
    "discipline_router",
    "enums",
    "health_router",
    "legacy_router",
    "meals_router",
    "models",
    "schemas",
    "service",
    "transport_router",
]
