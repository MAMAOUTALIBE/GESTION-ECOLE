"""Celery tasks for the attendance module — Module 3.

Tâche principale : ``ensure_attendance_partitions_task``.
Pré-crée les partitions mensuelles futures de ``AttendanceRecord`` pour
que les insertions du jour ne tombent jamais dans la partition ``_default``
(plus lente, moins prunable). Idempotent : si toutes les partitions
nécessaires existent déjà, l'appel ne fait rien d'autre qu'un SELECT sur
``pg_inherits``.

Configuration Celery beat
-------------------------
La tâche n'est pas auto-schedulée ici : pour l'activer en cron quotidien,
ajouter dans ``app/core/celery_app.py`` (ou un fichier ``beat_schedule.py``
dédié) :

    from celery.schedules import crontab

    celery_app.conf.beat_schedule = {
        "attendance-ensure-partitions": {
            "task": "attendance.ensure_partitions",
            "schedule": crontab(hour=2, minute=0),  # 02:00 UTC tous les jours
            "kwargs": {"months_ahead": 3},
        },
    }

Cette wiring n'est pas faite dans cette PR pour limiter l'impact sur le
boot Celery worker existant — il suffit d'ajouter ces 6 lignes le jour où
le service ops met en place le beat.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.modules.attendance.partitions import ensure_future_partitions


def _async_session_factory() -> async_sessionmaker:
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _ensure(months_ahead: int) -> list[str]:
    factory = _async_session_factory()
    async with factory() as session:
        created = await ensure_future_partitions(session, months_ahead=months_ahead)
        await session.commit()
        return created


@celery_app.task(name="attendance.ensure_partitions", bind=True, max_retries=3)
def ensure_attendance_partitions_task(
    self: Any, months_ahead: int = 3
) -> dict[str, Any]:
    """Crée les partitions manquantes pour les ``months_ahead`` mois futurs.

    Tâche bornée (quelques requêtes DDL au max), pas de retry exponentiel
    agressif : on retry 3 fois avec backoff linéaire.
    """
    try:
        loop = asyncio.new_event_loop()
        try:
            created = loop.run_until_complete(_ensure(months_ahead))
        finally:
            loop.close()
        return {"ok": True, "created": created, "count": len(created)}
    except Exception as exc:
        raise self.retry(
            exc=exc, countdown=60 * (self.request.retries + 1)
        ) from exc


__all__ = ["ensure_attendance_partitions_task"]
