"""Module 3B — Router HTTP du simulateur what-if.

Endpoints
---------
* ``POST   /api/simulator/scenarios``                — création.
* ``POST   /api/simulator/scenarios/{id}/compute``   — applique opérations
  + calcul d'impact + persistance impactJson.
* ``GET    /api/simulator/scenarios``                — liste filtrée RBAC.
* ``GET    /api/simulator/scenarios/{id}``           — get unique.
* ``POST   /api/simulator/scenarios/{id}/archive``   — archive.

RBAC
----
* Create / compute / archive : NATIONAL / MINISTRY / REGIONAL_ADMIN.
* List / get : authentifié, filtrage de scope appliqué dans le service.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.modules.auth.models import User
from app.modules.simulator.schemas import (
    ImpactReport,
    ScenarioCreate,
    ScenarioRead,
)
from app.modules.simulator.service import SimulatorService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

# Rôles HTTP autorisés à manipuler les scénarios. On garde la liste en
# constante module-level pour faciliter les tests d'import.
SIMULATOR_WRITE_HTTP_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
)


def _service(session: DbSession) -> SimulatorService:
    return SimulatorService(session)


SimulatorSvc = Annotated[SimulatorService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["simulator"])


@router.post(
    "/scenarios",
    response_model=ScenarioRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*SIMULATOR_WRITE_HTTP_ROLES))],
    summary="Crée un scénario what-if (DRAFT, calcul à venir)",
)
async def create_scenario(
    payload: ScenarioCreate,
    user: CurrentUserDep,
    service: SimulatorSvc,
) -> ScenarioRead:
    return await service.create_scenario(payload, user)


@router.post(
    "/scenarios/{scenario_id}/compute",
    response_model=ImpactReport,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*SIMULATOR_WRITE_HTTP_ROLES))],
    summary="Calcule l'impact du scénario + persiste impactJson",
)
async def compute_scenario(
    scenario_id: str,
    user: CurrentUserDep,
    service: SimulatorSvc,
) -> ImpactReport:
    return await service.compute_scenario(scenario_id, user)


@router.get(
    "/scenarios",
    response_model=list[ScenarioRead],
    summary="Liste les scénarios visibles par l'utilisateur",
)
async def list_scenarios(
    user: CurrentUserDep,
    service: SimulatorSvc,
) -> list[ScenarioRead]:
    return await service.list_scenarios(user)


@router.get(
    "/scenarios/{scenario_id}",
    response_model=ScenarioRead,
    summary="Renvoie un scénario par id (RBAC : créateur ou central)",
)
async def get_scenario(
    scenario_id: str,
    user: CurrentUserDep,
    service: SimulatorSvc,
) -> ScenarioRead:
    return await service.get_scenario(scenario_id, user)


@router.post(
    "/scenarios/{scenario_id}/archive",
    response_model=ScenarioRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*SIMULATOR_WRITE_HTTP_ROLES))],
    summary="Archive un scénario (statut ARCHIVED)",
)
async def archive_scenario(
    scenario_id: str,
    user: CurrentUserDep,
    service: SimulatorSvc,
) -> ScenarioRead:
    return await service.archive_scenario(scenario_id, user)


__all__ = ["SIMULATOR_WRITE_HTTP_ROLES", "router"]
