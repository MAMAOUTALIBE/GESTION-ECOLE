"""Celery tasks for the notifications module.

Each task runs an asyncio loop, opens a fresh DB session, loads the
``ParentCommunication`` row and dispatches it via the right channel adapter.
On failure the row is flipped to ``FAILED`` with an AuditLog row for context.
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


async def _dispatch_one(comm_id: str) -> dict[str, Any]:
    from app.core.observability import notification_dispatch_total
    from app.modules.notifications.channels.base import ChannelMessage
    from app.modules.notifications.dispatcher import dispatch
    from app.modules.notifications.service import NotificationsService

    factory = _async_session_factory()
    async with factory() as session:
        service = NotificationsService(session)
        try:
            payload = await service.load_dispatch_payload(comm_id)
        except Exception as exc:  # noqa: BLE001
            await service.mark_failed(comm_id, f"load_error:{exc}")
            await session.commit()
            notification_dispatch_total.labels(
                channel="unknown", result="failed"
            ).inc()
            return {"id": comm_id, "ok": False, "error": str(exc)}

        msg = ChannelMessage(
            recipient=payload["recipient"],
            message=payload["message"],
            subject=payload["subject"],
        )
        result = await dispatch(payload["channel"], msg, session=session)

        if result.ok:
            await service.mark_sent(comm_id, result.provider_id)
        else:
            await service.mark_failed(comm_id, result.error or "unknown_error")
        await session.commit()
        notification_dispatch_total.labels(
            channel=payload["channel"].value,
            result="ok" if result.ok else "failed",
        ).inc()
        return {
            "id": comm_id,
            "ok": result.ok,
            "providerId": result.provider_id,
            "error": result.error,
        }


@celery_app.task(name="notif.dispatch_communication", bind=True, max_retries=3)
def dispatch_communication(self, comm_id: str) -> dict[str, Any]:
    """Dispatch a single ParentCommunication. Retries with exp backoff on
    unexpected exceptions (the dispatcher itself never raises — exceptions
    here mean DB / event-loop trouble, worth retrying).
    """
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_dispatch_one(comm_id))
        finally:
            loop.close()
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=10 * (2 ** self.request.retries)) from exc


@celery_app.task(name="notif.dispatch_communications_batch", bind=True)
def dispatch_communications_batch(
    self, comm_ids: list[str]
) -> dict[str, Any]:
    """Dispatch N communications sequentially in this worker process.

    For very large batches you can chain this into a Celery ``group`` of
    individual ``dispatch_communication`` calls — useful when you want
    horizontal parallelism across workers.
    """
    succeeded: list[str] = []
    failed: list[dict[str, Any]] = []

    for index, comm_id in enumerate(comm_ids):
        self.update_state(
            state="PROGRESS",
            meta={"current": index + 1, "total": len(comm_ids), "id": comm_id},
        )
        try:
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(_dispatch_one(comm_id))
            finally:
                loop.close()
            if result.get("ok"):
                succeeded.append(comm_id)
            else:
                failed.append(
                    {"id": comm_id, "error": result.get("error", "unknown")}
                )
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": comm_id, "error": str(exc)})

    return {
        "total": len(comm_ids),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "failures": failed,
    }
