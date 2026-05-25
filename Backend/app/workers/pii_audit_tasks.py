"""Module 5C — Tâches Celery de l'audit PII.

Beat
----
Une seule tâche périodique : ``pii_audit.purge_old_logs``. Cible
mensuelle suggérée — le 15 du mois à 03:30 UTC (heure creuse + jour
neutre) :

.. code-block:: python

    from celery.schedules import crontab

    celery_app.conf.beat_schedule = {
        "purge-old-pii-audit-logs": {
            "task": "pii_audit.purge_old_logs",
            "schedule": crontab(day_of_month="15", hour=3, minute=30),
        },
    }

La purge supprime tout ``PiiAccessLog`` plus ancien que
``PII_LOG_RETENTION_DAYS`` (1095 j = 3 ans), conformément à la loi
037/AN/2016 + RGPD Art. 5(1)(e) (minimisation temporelle).
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.modules.pii_audit.enums import PII_LOG_RETENTION_DAYS
from app.modules.pii_audit.models import PiiAccessLog


def _async_session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        str(settings.database_url), pool_pre_ping=True,
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def _run_purge(retention_days: int) -> dict[str, Any]:
    """Effectue la purge — utilisé par le décorateur Celery + tests."""
    factory = _async_session_factory()
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    async with factory() as session:
        try:
            result = await session.execute(
                delete(PiiAccessLog).where(
                    PiiAccessLog.accessedAt < cutoff
                )
            )
            await session.commit()
            return {
                "ok": True,
                "cutoffDate": cutoff.isoformat(),
                "deleted": int(result.rowcount or 0),
            }
        except Exception as exc:
            await session.rollback()
            return {"ok": False, "error": str(exc)}


@celery_app.task(
    name="pii_audit.purge_old_logs", bind=True, max_retries=2,
)
def purge_old_pii_logs_task(
    self, retention_days: int | None = None,
) -> dict[str, Any]:
    """Beat mensuelle. Supprime les rows ``accessedAt < now - retention``."""
    days = int(retention_days or PII_LOG_RETENTION_DAYS)
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run_purge(days))
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(
            exc=exc, countdown=60 * (2 ** self.request.retries),
        ) from exc


__all__ = ["purge_old_pii_logs_task"]
