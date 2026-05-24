"""Celery tasks for PDF generation (bulletins).

Module 4 adds the single-card async pipeline ``generate_report_pdf_task``,
which is invoked by ``ReportsService.request_generation``. It performs:
1. Mark the ``ReportCard`` row as ``PROCESSING`` (so concurrent polls see it).
2. Load related entities + grades inside a sync SQLAlchemy session.
3. Render the HTML → PDF via WeasyPrint.
4. Compute SHA-256 + upload to ``s3://<bucket>/bulletins/<school>/<period>/<student>.pdf``.
5. Mark ``DONE`` + populate the metadata columns; bump Prometheus counters.
6. On exception: mark ``FAILED`` (after retries are exhausted) with the
   message in ``pdfErrorMessage``.

Retry policy
------------
We retry on ``S3Error`` (transient bucket / network issues) up to 3 times
with exponential backoff (1s, 5s, 25s). Render errors (e.g. malformed grade
data) are *not* retried — they're written to ``pdfErrorMessage`` directly.
"""
import asyncio
import base64
import time
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from celery import group  # noqa: F401  (kept for future per-item parallelism)
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.core.celery_app import celery_app
from app.core.config import settings
from app.modules.reports import storage
from app.modules.reports.storage import S3Error
from app.shared.enums import ReportCardPdfStatus


def _async_session_factory() -> async_sessionmaker:
    """Build a fresh async engine per worker process — Celery forks workers."""
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


def _sync_session_factory() -> sessionmaker[Session]:
    """Synchronous session for Celery tasks. Uses ``database_url_sync`` so we
    can avoid the asyncio overhead inside the worker (which is already running
    in its own process).
    """
    engine = create_engine(str(settings.database_url_sync), pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False, class_=Session)


def _maybe_upload_pdf(pdf_bytes: bytes, key: str) -> str | None:
    """Upload to S3/MinIO if creds are configured. Returns the object URL.

    Kept for the legacy ``render_bulletin`` / ``render_bulletins_batch`` tasks.
    The Module-4 path goes through :mod:`app.modules.reports.storage` directly.
    """
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
    """Render a single bulletin → (id, pdf_bytes_or_None, error_or_None).

    Used by the LEGACY batch task — the new ``generate_report_pdf_task`` uses
    a synchronous session for simplicity.
    """
    from weasyprint import HTML

    from app.modules.reports.service import ReportsService

    factory = _async_session_factory()
    async with factory() as session:
        service = ReportsService(session)
        html_str = await service.render_html_for_card(rc_id)
        if html_str is None:
            return rc_id, None, "Bulletin introuvable"

    pdf = HTML(string=html_str).write_pdf()
    return rc_id, pdf, None


# ---------------------------------------------------------------------------
# Module 4 — synchronous helpers used inside generate_report_pdf_task
# ---------------------------------------------------------------------------
def _load_report_card_sync(session: Session, rc_id: str) -> Any:
    from app.modules.academics.models import ReportCard
    from app.modules.census.models import Student
    from app.modules.schools.models import School

    stmt = (
        select(ReportCard)
        .where(ReportCard.id == rc_id)
        .options(
            selectinload(ReportCard.student)
            .selectinload(Student.school)
            .selectinload(School.region),
            selectinload(ReportCard.student).selectinload(Student.classRoom),
            selectinload(ReportCard.schoolYear),
            selectinload(ReportCard.period),
        )
    )
    return session.execute(stmt).scalar_one_or_none()


def _load_grades_sync(session: Session, rc: Any) -> list[dict[str, Any]]:
    from app.modules.academics.models import Grade

    stmt = (
        select(Grade)
        .where(
            Grade.studentId == rc.studentId,
            Grade.periodId == rc.periodId,
            Grade.schoolYearId == rc.schoolYearId,
        )
        .options(selectinload(Grade.subject), selectinload(Grade.assessment))
    )
    rows = session.execute(stmt).scalars().unique().all()
    per_subject: dict[str, dict[str, Any]] = {}
    for g in rows:
        subj = g.subject
        if not subj:
            continue
        current = per_subject.setdefault(
            subj.id,
            {
                "subject": subj.name,
                "coefficient": subj.coefficient,
                "max_score": int(g.assessment.maxScore) if g.assessment else 20,
                "weighted_sum": 0.0,
                "coef_sum": 0.0,
                "appreciations": [],
            },
        )
        assess_coef = g.assessment.coefficient if g.assessment else 1.0
        current["weighted_sum"] += g.score * assess_coef
        current["coef_sum"] += assess_coef
        if g.appreciation:
            current["appreciations"].append(g.appreciation)

    out: list[dict[str, Any]] = []
    for s in per_subject.values():
        score = (
            round((s["weighted_sum"] / s["coef_sum"]) * 100) / 100
            if s["coef_sum"] else 0.0
        )
        out.append({
            "subject": s["subject"],
            "coefficient": s["coefficient"],
            "max_score": s["max_score"],
            "score": score,
            "appreciation": " · ".join(s["appreciations"][:2]) if s["appreciations"] else "",
        })
    out.sort(key=lambda r: r["subject"])
    return out


def _render_pdf_sync(rc: Any, grades: list[dict[str, Any]]) -> bytes:
    """Render the HTML for a ReportCard + grades and produce PDF bytes."""
    from weasyprint import HTML

    from app.modules.reports.service import ReportsService

    # ReportsService._render_html_with_grades is stateless w.r.t. the session,
    # but it's a method; instantiate the service with a None session — no DB
    # work happens inside the render call.
    svc = ReportsService.__new__(ReportsService)
    svc.session = None  # type: ignore[assignment]
    html_str = svc._render_html_with_grades(rc, grades)
    return HTML(string=html_str).write_pdf()  # type: ignore[no-any-return]


@celery_app.task(
    name="pdf.generate_report_pdf",
    bind=True,
    max_retries=3,
    autoretry_for=(),  # we handle retry semantics manually
)
def generate_report_pdf_task(self, report_card_id: str) -> dict[str, Any]:
    """Génère un bulletin PDF, l'upload S3, met à jour le ReportCard.

    Voir l'en-tête du module pour la doc complète du flow.
    """
    # Imports locaux pour minimiser le coût du démarrage worker.
    import hashlib

    from app.core.observability import (
        reports_pdf_completed_total,
        reports_pdf_duration_seconds,
    )

    session_factory = _sync_session_factory()
    start = time.monotonic()
    with session_factory() as session:
        try:
            rc = _load_report_card_sync(session, report_card_id)
            if rc is None:
                # Pas la peine de retry — la ligne n'existe pas.
                return {"id": report_card_id, "ok": False, "error": "not_found"}

            # Mark PROCESSING + commit immédiat pour que le poll voie la transition.
            rc.pdfStatus = ReportCardPdfStatus.PROCESSING
            rc.pdfErrorMessage = None
            session.commit()

            grades = _load_grades_sync(session, rc)
            pdf_bytes = _render_pdf_sync(rc, grades)
            sha = hashlib.sha256(pdf_bytes).hexdigest()

            key = storage.bulletin_key(
                rc.student.schoolId, rc.periodId, rc.studentId
            )
            try:
                storage.upload_pdf_sync(
                    key,
                    pdf_bytes,
                    metadata={
                        "sha256": sha,
                        "report_card_id": rc.id,
                        "student_id": rc.studentId,
                        "period_id": rc.periodId,
                    },
                )
            except S3Error as exc:
                # Transient S3 issue — retry with exponential backoff.
                attempts = self.request.retries
                if attempts < self.max_retries:
                    countdown = 1 * (5 ** attempts)  # 1, 5, 25
                    raise self.retry(exc=exc, countdown=countdown) from exc
                # Out of retries → mark FAILED, do not crash.
                rc.pdfStatus = ReportCardPdfStatus.FAILED
                rc.pdfErrorMessage = f"S3 unavailable after {attempts + 1} attempts: {exc}"
                session.commit()
                reports_pdf_completed_total.labels(status="failed").inc()
                return {"id": rc.id, "ok": False, "error": "s3_unavailable"}

            rc.pdfStatus = ReportCardPdfStatus.DONE
            rc.pdfS3Key = key
            rc.pdfSha256 = sha
            rc.pdfGeneratedAt = datetime.now(UTC)
            rc.pdfErrorMessage = None
            session.commit()

            duration = time.monotonic() - start
            reports_pdf_duration_seconds.observe(duration)
            reports_pdf_completed_total.labels(status="done").inc()

            return {
                "id": rc.id,
                "ok": True,
                "size": len(pdf_bytes),
                "sha256": sha,
                "key": key,
                "durationSeconds": round(duration, 3),
            }
        except S3Error:
            # Propagated above — should not be reached. Defensive only.
            raise
        except Exception as exc:
            # Erreur non-recoverable (rendu, DB, ...) — on marque FAILED.
            try:
                rc = _load_report_card_sync(session, report_card_id)
                if rc is not None:
                    rc.pdfStatus = ReportCardPdfStatus.FAILED
                    rc.pdfErrorMessage = f"{type(exc).__name__}: {exc}"[:2000]
                    session.commit()
            except Exception:  # pragma: no cover - defensive
                session.rollback()
            reports_pdf_completed_total.labels(status="failed").inc()
            return {"id": report_card_id, "ok": False, "error": str(exc)}


@celery_app.task(name="pdf.render_bulletin", bind=True, max_retries=3)
def render_bulletin(self, rc_id: str) -> dict[str, Any]:
    """Render a single bulletin PDF and (optionally) upload to S3.

    LEGACY — kept for backward compatibility. New code should use
    ``generate_report_pdf_task`` which writes to ReportCard.pdf* columns.
    """
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
    except Exception as exc:
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
        except Exception as exc:
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
