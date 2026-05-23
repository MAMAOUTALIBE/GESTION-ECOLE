"""Celery tasks for PDF generation (bulletins).

The batch task is bound so progress can be reported via task state. PDFs are
optionally uploaded to S3/MinIO when the bucket is configured; otherwise they
are returned as base64 inline in the result (useful in dev).
"""
import asyncio
import base64
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from celery import group  # noqa: F401  (kept for future per-item parallelism)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings


def _async_session_factory() -> async_sessionmaker:
    """Build a fresh async engine per worker process — Celery forks workers."""
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


def _maybe_upload_pdf(pdf_bytes: bytes, key: str) -> str | None:
    """Upload to S3/MinIO if creds are configured. Returns the object URL."""
    if not (settings.s3_endpoint_url and settings.s3_access_key and settings.s3_secret_key):
        return None
    try:
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        # Best-effort bucket creation (idempotent)
        try:
            client.head_bucket(Bucket=settings.s3_bucket_reports)
        except ClientError:
            client.create_bucket(Bucket=settings.s3_bucket_reports)

        client.put_object(
            Bucket=settings.s3_bucket_reports,
            Key=key,
            Body=pdf_bytes,
            ContentType="application/pdf",
            Metadata={"generator": "GESTION-EE"},
        )
        return f"{settings.s3_endpoint_url}/{settings.s3_bucket_reports}/{key}"
    except (BotoCoreError, ClientError) as exc:
        # Don't crash the worker on S3 failure — return None so caller can retry
        return f"upload_error:{exc.__class__.__name__}"


async def _render_one(rc_id: str) -> tuple[str, bytes | None, str | None]:
    """Render a single bulletin → (id, pdf_bytes_or_None, error_or_None)."""
    from weasyprint import HTML  # noqa: PLC0415

    from app.modules.reports.service import ReportsService  # noqa: PLC0415

    factory = _async_session_factory()
    async with factory() as session:
        service = ReportsService(session)
        html_str = await service.render_html_for_card(rc_id)
        if html_str is None:
            return rc_id, None, "Bulletin introuvable"

    pdf = HTML(string=html_str).write_pdf()
    return rc_id, pdf, None


@celery_app.task(name="pdf.render_bulletin", bind=True, max_retries=3)
def render_bulletin(self, rc_id: str) -> dict[str, Any]:
    """Render a single bulletin PDF and (optionally) upload to S3."""
    try:
        loop = asyncio.new_event_loop()
        try:
            _id, pdf, error = loop.run_until_complete(_render_one(rc_id))
        finally:
            loop.close()

        if error or pdf is None:
            return {"id": rc_id, "ok": False, "error": error or "render failed"}

        url = _maybe_upload_pdf(pdf, key=f"{rc_id}.pdf")
        result: dict[str, Any] = {"id": rc_id, "ok": True, "size": len(pdf)}
        if url:
            result["url"] = url
        else:
            # Inline base64 — caller may persist as needed
            result["base64"] = base64.b64encode(pdf).decode("ascii")
        return result
    except Exception as exc:  # noqa: BLE001
        raise self.retry(exc=exc, countdown=10 * (2 ** self.request.retries))


@celery_app.task(name="pdf.render_bulletins_batch", bind=True)
def render_bulletins_batch(
    self, rc_ids: list[str], requested_by: str | None = None
) -> dict[str, Any]:
    """Render N bulletins in this worker process. For very large batches,
    consider splitting into chained groups (one task per bulletin).
    """
    succeeded: list[str] = []
    failed: list[dict[str, Any]] = []

    for index, rc_id in enumerate(rc_ids):
        self.update_state(
            state="PROGRESS",
            meta={"current": index + 1, "total": len(rc_ids), "id": rc_id},
        )
        try:
            loop = asyncio.new_event_loop()
            try:
                _id, pdf, error = loop.run_until_complete(_render_one(rc_id))
            finally:
                loop.close()
            if error or pdf is None:
                failed.append({"id": rc_id, "error": error or "render failed"})
                continue
            _maybe_upload_pdf(pdf, key=f"{rc_id}.pdf")
            succeeded.append(rc_id)
        except Exception as exc:  # noqa: BLE001
            failed.append({"id": rc_id, "error": str(exc)})

    return {
        "total": len(rc_ids),
        "succeeded": len(succeeded),
        "failed": len(failed),
        "failures": failed,
        "requestedBy": requested_by,
    }


@celery_app.task(name="pdf.noop")
def noop() -> str:
    """Placeholder retained for compatibility."""
    return "pdf.noop ok"
