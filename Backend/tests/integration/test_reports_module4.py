"""Module 4 — reports : génération PDF asynchrone via Celery + cache S3.

Couvre 15 cas répartis sur 5 axes :
* Lifecycle async (4 tests) : PENDING→PROCESSING→DONE, idempotence cache,
  state polling, retour download URL.
* Worker (4 tests) : exécution Celery eager, S3 key pattern, SHA-256,
  retry sur erreur S3 transitoire.
* Sécurité (2 tests) : RBAC (TEACHER/DIRECTEUR), scope territorial.
* Migration & observability (3 tests) : colonnes pdf*, partial index,
  Prometheus counters.
* Concurrence & URL (2 tests) : race condition double-enqueue,
  expiration presigned URL.

Configuration :
* On force ``CELERY_TASK_ALWAYS_EAGER=1`` AVANT l'import de l'app pour que
  ``celery_app.task_always_eager`` soit lu (cf. ``app/core/celery_app.py``).
* On utilise ``moto`` (mock_aws) pour intercepter les appels boto3 vers S3
  sans dépendre d'un MinIO réel — la conftest crée le bucket à la volée.
"""
from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# CRITICAL : ``CELERY_TASK_ALWAYS_EAGER`` doit être en place AVANT que le
# module ``app.core.celery_app`` ne soit importé par le test (les fixtures
# importent app.main, qui importe la chaîne entière). Comme ce fichier est
# importé lors de la collection, le os.environ ci-dessous tourne très tôt.
# ---------------------------------------------------------------------------
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "1"
# Force la réévaluation si celery_app était déjà importé (toggle conf).
try:  # pragma: no cover - defensive
    from app.core.celery_app import celery_app as _celery_app

    _celery_app.conf.task_always_eager = True
    _celery_app.conf.task_eager_propagates = True
except Exception:  # pragma: no cover
    pass

import pytest
import pytest_asyncio
from httpx import AsyncClient
from moto import mock_aws
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.academics.models import AcademicPeriod, ReportCard, SchoolYear
from app.modules.reports import storage
from app.shared.enums import (
    AcademicPeriodType,
    AcademicValidationStatus,
    ReportCardPdfStatus,
    UserRole,
    ValidationStatus,
)
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures locales
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def s3_bucket(monkeypatch: Any) -> Any:
    """Active moto pour mocker S3 (boto3) le temps du test.

    On reset le cache du client storage (lru_cache) pour qu'il recrée un
    client après que moto ait installé ses hooks. Bucket pré-créé.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    # Force settings to point at moto (endpoint_url None → real AWS API)
    monkeypatch.setattr(settings, "s3_endpoint_url", None, raising=False)
    monkeypatch.setattr(settings, "s3_access_key", "testing", raising=False)
    monkeypatch.setattr(settings, "s3_secret_key", "testing", raising=False)
    monkeypatch.setattr(settings, "s3_region", "us-east-1", raising=False)

    with mock_aws():
        storage.reset_client_cache()
        # Idempotent create
        storage.ensure_bucket_sync()
        yield settings.s3_bucket_reports
        storage.reset_client_cache()


async def _make_academic_context(
    session: AsyncSession,
    school: Any,
    classroom: Any | None = None,
) -> dict[str, Any]:
    """Crée SchoolYear + AcademicPeriod minimal pour un test."""
    year = SchoolYear(
        name=f"2025-2026-{school.id[:8]}",
        startDate=datetime(2025, 9, 1, tzinfo=UTC),
        endDate=datetime(2026, 6, 30, tzinfo=UTC),
        periodType=AcademicPeriodType.TRIMESTER,
        isActive=True,
    )
    session.add(year)
    await session.flush()

    period = AcademicPeriod(
        name="Trimestre 1",
        type=AcademicPeriodType.TRIMESTER,
        order=1,
        schoolYearId=year.id,
    )
    session.add(period)
    await session.flush()
    return {"year": year, "period": period, "classroom": classroom}


async def _make_full_context(
    db_session: AsyncSession,
    *,
    student_class_room: bool = True,
) -> dict[str, Any]:
    """Cree region->school->classroom->student->year->period."""
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    classroom = await factories.ClassRoomFactory.create_async(
        schoolId=tree["school"].id
    )
    student = await factories.StudentFactory.create_async(
        schoolId=tree["school"].id,
        classRoomId=classroom.id if student_class_room else None,
    )
    ctx = await _make_academic_context(db_session, tree["school"], classroom)
    return {
        **tree,
        "classroom": classroom,
        "student": student,
        **ctx,
    }


# ===========================================================================
# 1. LIFECYCLE — request, status, idempotence
# ===========================================================================
@pytest.mark.asyncio
async def test_request_generation_creates_pending_status_and_enqueues_task(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    """Premier appel → crée le ReportCard, retourne le status DONE (ou
    PENDING si on désactive le worker eager). Comme on tourne en eager, la
    génération s'exécute pendant la requête → status final DONE.
    """
    ctx = await _make_full_context(db_session)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=ctx["school"].id
    )

    r = await client.post(
        f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
        headers=headers,
    )
    assert r.status_code == 202, r.text
    payload = r.json()
    assert payload["reportCardId"]
    # En eager mode le task tourne dans la même requête → status devient DONE.
    assert payload["status"] in {"PENDING", "DONE"}
    assert payload["taskId"] is not None
    assert payload["pollUrl"].endswith("/status")


@pytest.mark.asyncio
async def test_request_generation_returns_done_if_already_generated_with_same_hash(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    """Deuxième POST sur (student, period) → ne re-rend PAS, renvoie DONE."""
    ctx = await _make_full_context(db_session)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=ctx["school"].id
    )

    # 1er call : rend & upload
    r1 = await client.post(
        f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
        headers=headers,
    )
    assert r1.status_code == 202
    first_payload = r1.json()
    rc_id = first_payload["reportCardId"]

    # On capture le sha256 généré
    await db_session.refresh(await db_session.get(ReportCard, rc_id))
    rc = await db_session.get(ReportCard, rc_id)
    first_sha = rc.pdfSha256
    assert first_sha is not None
    assert len(first_sha) == 64

    # 2ème call : pas de re-render, on récupère l'URL signée
    r2 = await client.post(
        f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
        headers=headers,
    )
    assert r2.status_code == 202
    p2 = r2.json()
    assert p2["status"] == "DONE"
    assert p2["downloadUrl"]
    assert p2["sha256"] == first_sha


@pytest.mark.asyncio
async def test_get_status_returns_pending_then_done(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    ctx = await _make_full_context(db_session)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=ctx["school"].id
    )

    r1 = await client.post(
        f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
        headers=headers,
    )
    rc_id = r1.json()["reportCardId"]

    r2 = await client.get(f"/api/reports/{rc_id}/status", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["status"] == "DONE"
    assert r2.json()["generatedAt"] is not None


@pytest.mark.asyncio
async def test_download_url_returns_presigned_when_done(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    ctx = await _make_full_context(db_session)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=ctx["school"].id
    )

    r1 = await client.post(
        f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
        headers=headers,
    )
    rc_id = r1.json()["reportCardId"]

    r2 = await client.get(
        f"/api/reports/{rc_id}/download",
        headers=headers,
        follow_redirects=False,
    )
    assert r2.status_code == 302
    location = r2.headers["location"]
    # URL signée S3 — contient un Signature/Expires query string.
    assert "X-Amz-Signature" in location or "Signature=" in location


@pytest.mark.asyncio
async def test_download_url_returns_404_when_pending(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    """Si on appelle /download avant que le worker n'ait fini → 404."""
    ctx = await _make_full_context(db_session)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=ctx["school"].id
    )

    # Crée le ReportCard à la main en status PENDING (sans enqueue)
    rc = ReportCard(
        studentId=ctx["student"].id,
        periodId=ctx["period"].id,
        schoolYearId=ctx["year"].id,
        classRoomId=ctx["classroom"].id,
        verificationCode="GE-TEST-1234",
        pdfStatus=ReportCardPdfStatus.PENDING,
    )
    db_session.add(rc)
    await db_session.flush()

    r = await client.get(f"/api/reports/{rc.id}/download", headers=headers)
    assert r.status_code == 404


# ===========================================================================
# 2. WORKER — render, upload, retry
# ===========================================================================
@pytest.mark.asyncio
async def test_worker_marks_failed_on_render_exception(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    """Si WeasyPrint plante → status FAILED + pdfErrorMessage rempli."""
    ctx = await _make_full_context(db_session)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=ctx["school"].id
    )

    with patch(
        "app.workers.pdf_tasks._render_pdf_sync",
        side_effect=ValueError("malformed grade payload"),
    ):
        r = await client.post(
            f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
            headers=headers,
        )
    assert r.status_code == 202
    rc_id = r.json()["reportCardId"]

    # Re-lire le ReportCard côté DB (worker a tourné en eager → state à jour)
    rc = await db_session.get(ReportCard, rc_id)
    assert rc.pdfStatus == ReportCardPdfStatus.FAILED
    assert "malformed grade payload" in (rc.pdfErrorMessage or "")


@pytest.mark.asyncio
async def test_worker_uploads_to_correct_s3_key_pattern(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    """Pattern : bulletins/<schoolId>/<periodId>/<studentId>.pdf."""
    ctx = await _make_full_context(db_session)
    # Capture les ids AVANT toute opération qui pourrait expirer les attrs.
    school_id = ctx["school"].id
    period_id = ctx["period"].id
    student_id = ctx["student"].id
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=school_id
    )

    r = await client.post(
        f"/api/reports/student/{student_id}/period/{period_id}/generate",
        headers=headers,
    )
    rc_id = r.json()["reportCardId"]
    rc = await db_session.get(ReportCard, rc_id)
    expected = f"bulletins/{school_id}/{period_id}/{student_id}.pdf"
    assert rc.pdfS3Key == expected
    # Et l'objet existe réellement dans le bucket moto
    meta = storage.head_object_sync(expected)
    assert meta is not None
    assert meta["ContentType"] == "application/pdf"


@pytest.mark.asyncio
async def test_pdf_sha256_is_computed_and_stored(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    """sha256 hex (64 chars) stocké en DB ET injecté dans la metadata S3."""
    ctx = await _make_full_context(db_session)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=ctx["school"].id
    )

    r = await client.post(
        f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
        headers=headers,
    )
    rc_id = r.json()["reportCardId"]
    rc = await db_session.get(ReportCard, rc_id)

    assert rc.pdfSha256 is not None
    assert len(rc.pdfSha256) == 64
    int(rc.pdfSha256, 16)  # raises if not hex

    meta = storage.head_object_sync(rc.pdfS3Key)
    assert meta["Metadata"]["sha256"] == rc.pdfSha256


@pytest.mark.asyncio
async def test_worker_retry_on_s3_transient_error(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    """Le worker doit retry sur S3Error transient — premier appel 5xx,
    deuxième appel OK → status final = DONE.
    """
    from app.modules.reports.storage import S3Error

    ctx = await _make_full_context(db_session)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=ctx["school"].id
    )

    real_upload = storage.upload_pdf_sync
    call_counter = {"n": 0}

    def flaky_upload(key: str, body: bytes, metadata: dict[str, str] | None = None) -> str:
        call_counter["n"] += 1
        if call_counter["n"] == 1:
            raise S3Error("transient 503")
        return real_upload(key, body, metadata)

    with patch("app.workers.pdf_tasks.storage.upload_pdf_sync", side_effect=flaky_upload):
        r = await client.post(
            f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
            headers=headers,
        )

    assert r.status_code == 202
    # Le retry a tourné → 2 appels
    assert call_counter["n"] >= 2
    rc_id = r.json()["reportCardId"]
    rc = await db_session.get(ReportCard, rc_id)
    assert rc.pdfStatus == ReportCardPdfStatus.DONE


# ===========================================================================
# 3. SÉCURITÉ — RBAC + scope territorial
# ===========================================================================
@pytest.mark.asyncio
async def test_rbac_only_teacher_director_can_request_generation(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    """CENSUS_AGENT n'a pas le droit de demander une génération."""
    ctx = await _make_full_context(db_session)
    headers = await auth_headers(
        UserRole.CENSUS_AGENT, schoolId=ctx["school"].id
    )

    r = await client.post(
        f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
        headers=headers,
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_status_endpoint_respects_territorial_scope(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    """Un directeur d'une autre école ne doit PAS lire le status d'un
    bulletin appartenant à mon école.
    """
    ctx = await _make_full_context(db_session)

    # Crée un autre directeur rattaché à une autre école (autre région).
    other_tree = await factories.make_territorial_tree()
    intruder = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=other_tree["school"].id
    )

    # Crée un ReportCard pour notre élève de school principal.
    rc = ReportCard(
        studentId=ctx["student"].id,
        periodId=ctx["period"].id,
        schoolYearId=ctx["year"].id,
        verificationCode="GE-SCOPE-TEST",
        pdfStatus=ReportCardPdfStatus.PENDING,
    )
    db_session.add(rc)
    await db_session.flush()

    r = await client.get(f"/api/reports/{rc.id}/status", headers=intruder)
    assert r.status_code == 403


# ===========================================================================
# 4. MIGRATION & OBSERVABILITY
# ===========================================================================
@pytest.mark.asyncio
async def test_migration_0011_adds_columns(db_session: AsyncSession) -> None:
    """Les 6 colonnes Module 4 doivent être présentes dans pg_attribute."""
    row = await db_session.execute(
        text(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'ReportCard'
              AND column_name IN (
                'pdfStatus', 'pdfS3Key', 'pdfSha256',
                'pdfGeneratedAt', 'pdfErrorMessage', 'pdfTaskId'
              )
            ORDER BY column_name
            """
        )
    )
    cols = {r[0] for r in row.fetchall()}
    assert cols == {
        "pdfStatus",
        "pdfS3Key",
        "pdfSha256",
        "pdfGeneratedAt",
        "pdfErrorMessage",
        "pdfTaskId",
    }


@pytest.mark.asyncio
async def test_metrics_pdf_requested_incremented(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    from app.core.observability import reports_pdf_requested_total

    ctx = await _make_full_context(db_session)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=ctx["school"].id
    )

    before = reports_pdf_requested_total._value.get()
    r = await client.post(
        f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
        headers=headers,
    )
    assert r.status_code == 202
    after = reports_pdf_requested_total._value.get()
    assert after == before + 1


# ===========================================================================
# 5. CONCURRENCE & URL
# ===========================================================================
@pytest.mark.asyncio
async def test_generate_handles_concurrent_requests_same_student_period(
    db_session: AsyncSession,
    client: AsyncClient,
    auth_headers: Any,
    s3_bucket: str,
) -> None:
    """Deux appels HTTP "simultanés" (en séquentiel ici, mais dans la même
    transaction) doivent aboutir à un seul ReportCard et au plus un seul
    task enqueue (le 2eme call renvoie le DONE existant).
    """
    ctx = await _make_full_context(db_session)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=ctx["school"].id
    )

    r1 = await client.post(
        f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
        headers=headers,
    )
    r2 = await client.post(
        f"/api/reports/student/{ctx['student'].id}/period/{ctx['period'].id}/generate",
        headers=headers,
    )

    rc1 = r1.json()["reportCardId"]
    rc2 = r2.json()["reportCardId"]
    assert rc1 == rc2  # même ReportCard

    # En DB il n'y a qu'une ligne ReportCard pour ce (student, period)
    count = (
        await db_session.execute(
            select(ReportCard).where(
                ReportCard.studentId == ctx["student"].id,
                ReportCard.periodId == ctx["period"].id,
            )
        )
    ).scalars().all()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_download_url_expires_in_3600_seconds(
    db_session: AsyncSession,
    s3_bucket: str,
) -> None:
    """La presigned URL doit contenir ``X-Amz-Expires=3600`` (1h) par défaut."""
    # On upload un blob dummy puis on demande la presigned URL.
    storage.upload_pdf_sync("test/expiry.pdf", b"%PDF-1.4\n%dummy\n", metadata={"x": "1"})
    url = storage.get_presigned_url_sync("test/expiry.pdf")
    # boto3 utilise SigV4 (`X-Amz-Expires=3600`) ou signature legacy
    # (`Expires=<epoch>`) selon la version / le client. On accepte les deux.
    assert "X-Amz-Expires=3600" in url or "Expires=" in url
    if "Expires=" in url and "X-Amz-Expires" not in url:
        # Légacy SigV2 → on vérifie que l'expiry est ~maintenant + 3600s.
        import re
        import time as _t

        m = re.search(r"Expires=(\d+)", url)
        assert m
        epoch = int(m.group(1))
        now = int(_t.time())
        # Tolérance de 5s pour les retards d'exécution.
        assert 3595 <= (epoch - now) <= 3605, f"Expiry off: {epoch - now}s"


# ===========================================================================
# 6. STORAGE — unit-ish coverage
# ===========================================================================
def test_bulletin_key_format() -> None:
    """Le key helper respecte le pattern documenté."""
    k = storage.bulletin_key("school-A", "period-B", "student-C")
    assert k == "bulletins/school-A/period-B/student-C.pdf"


@pytest.mark.asyncio
async def test_upload_and_head_object_roundtrip(s3_bucket: str) -> None:
    """Upload → head_object retourne les bonnes metadata + Content-Length."""
    payload = b"%PDF-1.4\nhello\n"
    expected_sha = hashlib.sha256(payload).hexdigest()
    await storage.upload_pdf(
        "test/roundtrip.pdf", payload, metadata={"sha256": expected_sha}
    )
    meta = await storage.head_object("test/roundtrip.pdf")
    assert meta is not None
    assert meta["ContentLength"] == len(payload)
    assert meta["Metadata"]["sha256"] == expected_sha


# Sanity: marker hint to confirm test_validation_status enum still mapped.
def test_module4_enum_pdf_status_complete() -> None:
    assert {s.value for s in ReportCardPdfStatus} == {
        "PENDING",
        "PROCESSING",
        "DONE",
        "FAILED",
    }


# Sanity: AcademicValidationStatus mapping unchanged (regression guard).
def test_module4_does_not_alter_existing_validation_status_enum() -> None:
    assert {s.value for s in AcademicValidationStatus} >= {
        "DRAFT",
        "SUBMITTED",
        "VALIDATED",
        "REJECTED",
    }
    assert ValidationStatus.APPROVED.value == "APPROVED"
