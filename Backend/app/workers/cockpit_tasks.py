"""Module 19 — Tâches Celery du cockpit ministériel.

Beat
----
Une seule tâche : ``cockpit.snapshot_daily_kpis``. À planifier quotidien
à 00:30 UTC (juste après minuit pour capter le jour J-1 complet).

.. code-block:: python

    from celery.schedules import crontab

    celery_app.conf.beat_schedule = {
        "snapshot-daily-cockpit-kpis": {
            "task": "cockpit.snapshot_daily_kpis",
            "schedule": crontab(hour=0, minute=30),
        },
    }
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings


def _async_session_factory() -> async_sessionmaker:
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _run_snapshot(snapshot_date: date | None) -> dict[str, Any]:
    from app.modules.cockpit.service import CockpitService

    factory = _async_session_factory()
    async with factory() as session:
        svc = CockpitService(session)
        try:
            result = await svc.snapshot_daily_kpis(snapshot_date=snapshot_date)
            await session.commit()
            return {
                "ok": True,
                "snapshotDate": result.snapshotDate.isoformat(),
                "persisted": result.persisted,
                "keys": result.keys,
            }
        except Exception as exc:
            await session.rollback()
            return {"ok": False, "error": str(exc)}


@celery_app.task(name="cockpit.snapshot_daily_kpis", bind=True, max_retries=2)
def snapshot_daily_kpis_task(
    self, iso_date: str | None = None,
) -> dict[str, Any]:
    """Beat quotidien 00:30 UTC. Idempotent sur la date."""
    target = date.fromisoformat(iso_date) if iso_date else None
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run_snapshot(target))
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(
            exc=exc, countdown=60 * (2 ** self.request.retries),
        ) from exc


__all__ = ["snapshot_daily_kpis_task"]
