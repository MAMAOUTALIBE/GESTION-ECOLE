"""Celery tasks for the workflow module — currently SLA escalations.

Scheduling
----------
Add the following to ``celery beat`` (see deployment docs):

.. code-block:: python

    CELERY_BEAT_SCHEDULE = {
        "escalate-overdue-validations-daily": {
            "task": "workflow.escalate_overdue_validations",
            "schedule": crontab(hour=6, minute=0),  # 06:00 UTC every day
        },
    }

Each run opens a fresh DB session, queries every ``ValidationRequest``
past its SLA, and bumps their escalation level (with cross-channel
notifications) via :func:`app.modules.workflow.sla.escalate_request`.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings


def _async_session_factory() -> async_sessionmaker:
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _escalate_overdue() -> dict[str, Any]:
    from app.modules.notifications.service import NotificationsService
    from app.modules.workflow.sla import check_overdue_requests, escalate_request

    factory = _async_session_factory()
    summary: dict[str, Any] = {"checked": 0, "escalated": 0, "errors": []}

    async with factory() as session:
        overdue = await check_overdue_requests(session)
        summary["checked"] = len(overdue)
        notif_service = NotificationsService(session)

        async def _notifier(
            *,
            user_id: str,
            channel: str,
            template_key: str,
            variables: dict[str, object],
        ) -> None:
            try:
                await notif_service.send_via_template(
                    user_id=user_id,
                    channel=channel,
                    template_key=template_key,
                    variables=variables,
                )
            except Exception as exc:
                summary["errors"].append(
                    {
                        "user_id": user_id,
                        "channel": channel,
                        "error": str(exc),
                    }
                )

        for request in overdue:
            try:
                await escalate_request(session, request, _notifier)
                summary["escalated"] += 1
            except Exception as exc:
                summary["errors"].append(
                    {"request_id": request.id, "error": str(exc)}
                )
        await session.commit()
    return summary


@celery_app.task(name="workflow.escalate_overdue_validations", bind=True, max_retries=2)
def escalate_overdue_validations_task(self) -> dict[str, Any]:
    """Run :func:`_escalate_overdue` once. Designed to be scheduled by beat."""
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_escalate_overdue())
        finally:
            loop.close()
    except Exception as exc:
        raise self.retry(exc=exc, countdown=120) from exc
