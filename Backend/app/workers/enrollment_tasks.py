"""Module 1B — Tâches Celery du module Enrollment (GPI).

Beat (proposition ops)
----------------------
Une seule tâche : ``enrollment.compute_gpi_snapshots``. À planifier le
dimanche à 03:00 UTC (fenêtre de faible charge — la prod tourne en UTC).

.. code-block:: python

    from celery.schedules import crontab

    celery_app.conf.beat_schedule = {
        "compute-gpi-snapshots-weekly": {
            "task": "enrollment.compute_gpi_snapshots",
            "schedule": crontab(hour=3, minute=0, day_of_week=0),
        },
    }

La tâche est aussi déclenchable manuellement via ::

    from app.workers.enrollment_tasks import compute_gpi_snapshots_task
    compute_gpi_snapshots_task.delay("<schoolYearId>")   # ou None

Quand ``school_year_id`` est ``None`` la tâche recalcule pour la
``SchoolYear`` active (``isActive=True``) au moment du déclenchement.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings


def _async_session_factory() -> async_sessionmaker:
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _resolve_active_school_year(session: Any) -> str | None:
    """Renvoie l'id de la SchoolYear active courante, sinon None."""
    from app.modules.academics.models import SchoolYear

    stmt = select(SchoolYear.id).where(SchoolYear.isActive.is_(True)).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _resolve_system_admin(session: Any) -> Any | None:
    """Retourne un User NATIONAL_ADMIN (pour passer le check RBAC du service).

    Convention : la tâche est exécutée par un compte technique. Si aucun
    NATIONAL_ADMIN n'existe (cas extrême : install vierge), on renvoie
    None et la tâche échoue proprement avec un message explicite.
    """
    from app.modules.auth.models import User
    from app.shared.enums import UserRole

    stmt = (
        select(User)
        .where(User.role == UserRole.NATIONAL_ADMIN, User.isActive.is_(True))
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _run_snapshot(school_year_id: str | None) -> dict[str, Any]:
    from app.modules.enrollment.service import EnrollmentService

    factory = _async_session_factory()
    async with factory() as session:
        try:
            year_id = school_year_id or await _resolve_active_school_year(session)
            if year_id is None:
                return {"ok": False, "error": "Aucune SchoolYear active trouvée"}
            actor = await _resolve_system_admin(session)
            if actor is None:
                return {
                    "ok": False,
                    "error": (
                        "Aucun NATIONAL_ADMIN actif — créez un compte "
                        "admin avant de lancer cette tâche."
                    ),
                }

            svc = EnrollmentService(session)
            result = await svc.compute_gpi_snapshots(year_id, actor)
            await session.commit()
            return {
                "ok": True,
                "schoolYearId": result.schoolYearId,
                "persisted": result.persisted,
                "criticalAnomaliesCreated": result.criticalAnomaliesCreated,
            }
        except Exception as exc:
            await session.rollback()
            return {"ok": False, "error": str(exc)}


@celery_app.task(name="enrollment.compute_gpi_snapshots", bind=True, max_retries=2)
def compute_gpi_snapshots_task(
    self, school_year_id: str | None = None,
) -> dict[str, Any]:
    """Beat hebdomadaire dimanche 03:00 UTC — idempotent."""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run_snapshot(school_year_id))
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(
            exc=exc, countdown=60 * (2 ** self.request.retries),
        ) from exc


__all__ = ["compute_gpi_snapshots_task"]
