"""Module 2A — Tâches Celery du module Projections (transitions).

Pourquoi pas un beat automatique ?
----------------------------------
Le calcul d'un taux de transition suppose que l'année source ET l'année
cible soient toutes deux clôturées (recensement validé MEN). Un beat
auto risquerait de figer des rates sur une année toujours en cours de
saisie. La tâche est donc déclenchée **manuellement** par le cabinet :

.. code-block:: python

    from app.workers.projection_tasks import compute_transitions_task
    compute_transitions_task.delay(["<year2023>", "<year2024>"])

Si la liste est vide, la tâche échoue proprement (validation côté
service Pydantic ``min_length=1``).
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings


def _async_session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        str(settings.database_url), pool_pre_ping=True,
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def _resolve_system_admin(session: Any) -> Any | None:
    """Retourne un User NATIONAL_ADMIN actif (pour le check RBAC).

    Convention : la tâche est exécutée par un compte technique. Si
    aucun NATIONAL_ADMIN n'existe (install vierge), on renvoie None
    et la tâche échoue proprement avec un message explicite.
    """
    from app.modules.auth.models import User
    from app.shared.enums import UserRole

    stmt = (
        select(User)
        .where(
            User.role == UserRole.NATIONAL_ADMIN,
            User.isActive.is_(True),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _run_compute(
    school_year_from_ids: list[str],
) -> dict[str, Any]:
    from app.modules.projections.service import TransitionRateService

    factory = _async_session_factory()
    async with factory() as session:
        try:
            actor = await _resolve_system_admin(session)
            if actor is None:
                return {
                    "ok": False,
                    "error": (
                        "Aucun NATIONAL_ADMIN actif — créez un compte "
                        "admin avant de lancer cette tâche."
                    ),
                }

            svc = TransitionRateService(session)
            result = await svc.compute_transitions(
                school_year_from_ids, actor,
            )
            await session.commit()
            return {
                "ok": True,
                "computed": result.computed,
                "outliers": result.outliers,
                "anomaliesCreated": result.anomaliesCreated,
                "skipped": result.skipped,
            }
        except Exception as exc:
            await session.rollback()
            return {"ok": False, "error": str(exc)}


@celery_app.task(
    name="projections.compute_transitions", bind=True, max_retries=2,
)
def compute_transitions_task(
    self, school_year_from_ids: list[str],
) -> dict[str, Any]:
    """Recalcul manuel des taux de transition pour les années données.

    Pas de beat automatique : une année doit être clôturée par décision
    MEN avant que le calcul soit pertinent.
    """
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                _run_compute(school_year_from_ids),
            )
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(
            exc=exc, countdown=60 * (2 ** self.request.retries),
        ) from exc


# ===========================================================================
# Module 2B — Task run_projection_task
# ===========================================================================
async def _run_projection(
    base_school_year_id: str,
    horizon_years: int = 5,
    scenario_id: str = "BASELINE",
) -> dict[str, Any]:
    from app.modules.projections.schemas import RunProjectionRequest
    from app.modules.projections.service import ProjectionService

    factory = _async_session_factory()
    async with factory() as session:
        try:
            actor = await _resolve_system_admin(session)
            if actor is None:
                return {
                    "ok": False,
                    "error": (
                        "Aucun NATIONAL_ADMIN actif — créez un compte "
                        "admin avant de lancer cette tâche."
                    ),
                }

            svc = ProjectionService(session)
            result = await svc.run_projection(
                RunProjectionRequest(
                    baseSchoolYearId=base_school_year_id,
                    horizonYears=horizon_years,
                    scenarioId=scenario_id,
                ),
                actor,
            )
            await session.commit()
            return {
                "ok": True,
                "scenarioId": result.scenarioId,
                "projectedRows": result.projectedRows,
                "regionsCovered": result.regionsCovered,
                "horizonYears": result.horizonYears,
            }
        except Exception as exc:
            await session.rollback()
            return {"ok": False, "error": str(exc)}


@celery_app.task(
    name="projections.run_projection", bind=True, max_retries=2,
)
def run_projection_task(
    self,
    base_school_year_id: str,
    horizon_years: int = 5,
    scenario_id: str = "BASELINE",
) -> dict[str, Any]:
    """Calcule une projection horizon ``horizon_years`` ans.

    Déclenchée manuellement après ``compute_transitions_task`` :
    sans rates Module 2A persistés, la projection n'a aucun sens.
    """
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                _run_projection(
                    base_school_year_id, horizon_years, scenario_id,
                ),
            )
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(
            exc=exc, countdown=60 * (2 ** self.request.retries),
        ) from exc


__all__ = ["compute_transitions_task", "run_projection_task"]
