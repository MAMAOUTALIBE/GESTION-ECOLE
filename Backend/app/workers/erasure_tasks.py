"""Module 5D — Tâche Celery du droit à l'oubli.

Beat
----
Une tâche quotidienne ``erasure.execute_pending_erasures``. Cible
suggérée — tous les jours à 04:00 UTC :

.. code-block:: python

    from celery.schedules import crontab

    celery_app.conf.beat_schedule = {
        "execute-pending-erasures": {
            "task": "erasure.execute_pending_erasures",
            "schedule": crontab(hour=4, minute=0),
        },
    }

L'agent système (NATIONAL_ADMIN technique) qui porte cette tâche n'a
pas besoin d'un User réel : on charge un User SYSTEM_NATIONAL_ADMIN
(convention : email ``system+erasure@gestion-ee.gov.gn``). Si absent,
le batch logge un warning et ne fait rien (best-effort).
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings
from app.modules.auth.models import User
from app.modules.erasure.service import ErasureService
from app.shared.enums import UserRole

SYSTEM_EMAIL_HINT = "system+erasure@gestion-ee.gov.gn"


def _async_session_factory() -> async_sessionmaker:
    engine = create_async_engine(
        str(settings.database_url), pool_pre_ping=True,
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def _resolve_system_admin(session: Any) -> User | None:
    """Cherche un compte NATIONAL_ADMIN actif pour porter le batch.

    On préfère un compte technique dédié (``SYSTEM_EMAIL_HINT``), sinon
    on prend le premier NATIONAL_ADMIN actif disponible. Si rien
    trouvé → ``None`` (le caller skip proprement).
    """
    by_email = (
        await session.execute(
            select(User).where(User.email == SYSTEM_EMAIL_HINT)
        )
    ).scalars().one_or_none()
    if by_email is not None and by_email.isActive:
        return by_email
    fallback = (
        await session.execute(
            select(User).where(
                User.role == UserRole.NATIONAL_ADMIN,
                User.isActive.is_(True),
            )
        )
    ).scalars().first()
    return fallback


async def _run() -> dict[str, Any]:
    factory = _async_session_factory()
    async with factory() as session:
        actor = await _resolve_system_admin(session)
        if actor is None:
            logger.warning(
                "erasure.task: no NATIONAL_ADMIN found — skipping batch."
            )
            return {"ok": False, "reason": "no_national_admin"}
        service = ErasureService(session)
        try:
            result = await service.execute_pending_erasures(actor)
            await session.commit()
            return {"ok": True, **result}
        except Exception as exc:
            await session.rollback()
            logger.error("erasure.task: batch failed: {}", exc)
            return {"ok": False, "error": str(exc)}


@celery_app.task(
    name="erasure.execute_pending_erasures",
    bind=True,
    max_retries=2,
)
def execute_pending_erasures_task(self) -> dict[str, Any]:
    """Beat quotidienne — exécute les demandes éligibles."""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(
            exc=exc, countdown=60 * (2 ** self.request.retries),
        ) from exc


__all__ = ["execute_pending_erasures_task"]
