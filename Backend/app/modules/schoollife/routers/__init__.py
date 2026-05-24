"""4 routers métier Module 7 — montés séparément sous /api/schoollife/*."""
from app.modules.schoollife.routers.discipline import router as discipline_router
from app.modules.schoollife.routers.health import router as health_router
from app.modules.schoollife.routers.meals import router as meals_router
from app.modules.schoollife.routers.transport import router as transport_router

__all__ = [
    "discipline_router",
    "health_router",
    "meals_router",
    "transport_router",
]
