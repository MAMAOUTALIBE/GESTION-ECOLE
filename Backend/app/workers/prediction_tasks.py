"""Module 8 — Celery tasks pour le pipeline de prédiction.

Deux tâches :
* ``predict.batch_predict_school(school_id)`` — re-score tous les élèves
  d'une école. Déclenchée par le POST batch-predict pour les grosses
  écoles (> 500 élèves) et utilisable depuis Celery beat.
* ``predict.predict_all_schools()`` — itère sur toutes les écoles APPROVED
  et lance ``batch_predict_school`` pour chacune. Cible : tournée mensuelle
  pilotable via beat.
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


async def _run_batch_predict(school_id: str) -> dict[str, Any]:
    from app.modules.predictions.service import PredictionService

    factory = _async_session_factory()
    async with factory() as session:
        service = PredictionService(session)
        try:
            count = await service.batch_predict_school(school_id)
            await session.commit()
            return {"schoolId": school_id, "ok": True, "predicted": count}
        except Exception as exc:
            await session.rollback()
            return {"schoolId": school_id, "ok": False, "error": str(exc)}


@celery_app.task(name="predict.batch_predict_school", bind=True, max_retries=2)
def batch_predict_school_task(self, school_id: str) -> dict[str, Any]:
    """Re-score tous les élèves d'une école."""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run_batch_predict(school_id))
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries)) from exc


async def _run_all_schools() -> dict[str, Any]:
    from app.modules.schools.models import School
    from app.shared.enums import ValidationStatus

    factory = _async_session_factory()
    succeeded: list[str] = []
    failed: list[dict[str, Any]] = []
    async with factory() as session:
        ids_stmt = select(School.id).where(School.status == ValidationStatus.APPROVED)
        school_ids = list((await session.execute(ids_stmt)).scalars())

    # Déclenche les tâches enfants (chacune dans son propre process worker
    # selon la configuration Celery).
    for sid in school_ids:
        try:
            batch_predict_school_task.delay(sid)
            succeeded.append(sid)
        except Exception as exc:
            failed.append({"schoolId": sid, "error": str(exc)})

    return {
        "total": len(school_ids),
        "dispatched": len(succeeded),
        "failed": len(failed),
        "failures": failed,
    }


@celery_app.task(name="predict.predict_all_schools", bind=True)
def predict_all_schools_task(self) -> dict[str, Any]:
    """Tournée mensuelle : disperse les batch tasks sur toutes les écoles."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_all_schools())
    finally:
        loop.close()
