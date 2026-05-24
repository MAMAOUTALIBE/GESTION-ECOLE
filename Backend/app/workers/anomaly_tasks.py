"""Module 9 — Celery tasks pour la détection d'anomalies.

Deux tâches :

* ``anomalies.detect_anomalies_school(school_id)`` — exécute tous les
  détecteurs pour une école donnée. Utile pour rejouer une détection
  après nettoyage de données.
* ``anomalies.detect_anomalies_all()`` — balayage global. Cible :
  tournée HEBDOMADAIRE pilotée par Celery beat (le run est plus coûteux
  qu'une tournée mensuelle de scoring, mais reste raisonnable car les
  détecteurs sont SQL purs avec LIMIT).
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


async def _run_school(school_id: str) -> dict[str, Any]:
    from app.modules.anomalies.service import AnomalyService

    factory = _async_session_factory()
    async with factory() as session:
        service = AnomalyService(session)
        try:
            count = await service.run_all_detectors(school_id=school_id)
            await session.commit()
            return {"schoolId": school_id, "ok": True, "detected": count}
        except Exception as exc:
            await session.rollback()
            return {"schoolId": school_id, "ok": False, "error": str(exc)}


@celery_app.task(name="anomalies.detect_anomalies_school", bind=True, max_retries=2)
def detect_anomalies_school_task(self, school_id: str) -> dict[str, Any]:
    """Détection sur une école unique."""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run_school(school_id))
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries)) from exc


async def _run_all() -> dict[str, Any]:
    """Disperse les détections sur toutes les écoles APPROVED.

    On préfère dispatcher des sous-tâches par école plutôt qu'un seul
    run global : si une école a une corruption massive, on n'écroule pas
    les autres et on peut retry indépendamment.
    """
    from app.modules.schools.models import School
    from app.shared.enums import ValidationStatus

    factory = _async_session_factory()
    succeeded: list[str] = []
    failed: list[dict[str, Any]] = []
    async with factory() as session:
        ids_stmt = select(School.id).where(
            School.status == ValidationStatus.APPROVED
        )
        school_ids = list((await session.execute(ids_stmt)).scalars())

    for sid in school_ids:
        try:
            detect_anomalies_school_task.delay(sid)
            succeeded.append(sid)
        except Exception as exc:
            failed.append({"schoolId": sid, "error": str(exc)})

    return {
        "total": len(school_ids),
        "dispatched": len(succeeded),
        "failed": len(failed),
        "failures": failed,
    }


@celery_app.task(name="anomalies.detect_anomalies_all", bind=True)
def detect_anomalies_all_task(self) -> dict[str, Any]:
    """Tournée hebdomadaire : disperse les sous-tâches sur toutes les écoles."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run_all())
    finally:
        loop.close()
