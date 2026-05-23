"""Phase 5 contract tests — Attendance + Workflow + Census QR.

Pydantic validation + OpenAPI surface only. End-to-end tests with seed data
will live in tests/integration/ in a later phase.
"""
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.modules.attendance.schemas import ScanAttendanceRequest
from app.modules.census.service import CensusService
from app.modules.workflow.schemas import ReviewValidationRequestPayload
from app.shared.enums import AttendanceStatus, ValidationStatus


# ---------------------------------------------------------------------
# OpenAPI: every Phase 5 endpoint must be discoverable
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_openapi_exposes_phase5_endpoints(async_client: AsyncClient) -> None:
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    for url in (
        # Attendance
        "/api/attendance/today",
        "/api/attendance/scan",
        # Workflow (mounted at root, no per-controller prefix in NestJS)
        "/api/validation-requests",
        "/api/validation-requests/{request_id}/review",
        "/api/notifications",
        "/api/notifications/unread-count",
        "/api/notifications/{notification_id}/read",
        # Census QR identify (deferred from Phase 2 to here)
        "/api/census/identify/{token}",
        "/api/census/qr/{token}",
    ):
        assert url in paths, f"Missing endpoint: {url}"


# ---------------------------------------------------------------------
# Pydantic — Attendance validation
# ---------------------------------------------------------------------
def test_scan_attendance_requires_qr_token() -> None:
    with pytest.raises(ValidationError):
        ScanAttendanceRequest(qrToken="")  # min_length=1


def test_scan_attendance_default_status_is_none() -> None:
    """status is optional; service falls back to PRESENT when absent."""
    dto = ScanAttendanceRequest(qrToken="abc123")
    assert dto.status is None


def test_scan_attendance_accepts_explicit_status() -> None:
    dto = ScanAttendanceRequest(qrToken="abc123", status=AttendanceStatus.LATE)
    assert dto.status == AttendanceStatus.LATE


def test_scan_attendance_rejects_invalid_status() -> None:
    with pytest.raises(ValidationError):
        ScanAttendanceRequest.model_validate(
            {"qrToken": "abc", "status": "MAYBE"}
        )


def test_scan_attendance_strips_whitespace() -> None:
    dto = ScanAttendanceRequest(qrToken="  TOKEN  ")
    assert dto.qrToken == "TOKEN"


# ---------------------------------------------------------------------
# Pydantic — Workflow validation
# ---------------------------------------------------------------------
def test_review_request_requires_status() -> None:
    with pytest.raises(ValidationError):
        ReviewValidationRequestPayload.model_validate({})  # type: ignore[call-arg]


def test_review_request_accepts_optional_reason() -> None:
    dto = ReviewValidationRequestPayload(status=ValidationStatus.APPROVED)
    assert dto.reason is None


def test_review_request_min_reason_length() -> None:
    with pytest.raises(ValidationError):
        ReviewValidationRequestPayload(status=ValidationStatus.REJECTED, reason="x")


def test_review_request_accepts_valid_reason() -> None:
    dto = ReviewValidationRequestPayload(
        status=ValidationStatus.REJECTED, reason="Données incomplètes."
    )
    assert dto.reason == "Données incomplètes."


def test_review_request_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError):
        ReviewValidationRequestPayload.model_validate(
            {"status": "MAYBE", "reason": "test"}
        )


# ---------------------------------------------------------------------
# Pure-function: QR candidates parsing (mirror NestJS qrCandidates)
# ---------------------------------------------------------------------
def test_qr_candidates_simple_token() -> None:
    assert CensusService._qr_candidates("abc123") == ["abc123"]


def test_qr_candidates_strips_query() -> None:
    out = CensusService._qr_candidates("abc?lang=fr")
    assert out == ["abc?lang=fr", "abc"]


def test_qr_candidates_extracts_last_url_segment() -> None:
    out = CensusService._qr_candidates("https://gestionee.gn/qr/abc123")
    assert "abc123" in out
    assert "https://gestionee.gn/qr/abc123" in out


def test_qr_candidates_dedup_when_no_url() -> None:
    out = CensusService._qr_candidates("abc")
    assert out == ["abc"]  # deduped, not duplicated


# ---------------------------------------------------------------------
# QR SVG renderer — pure function
# ---------------------------------------------------------------------
def test_render_qr_svg_returns_xml() -> None:
    svg = CensusService._render_qr_svg("CODE-2026-000001")
    assert svg.startswith("<?xml") or svg.lstrip().startswith("<svg")
    assert "<svg" in svg
    assert "</svg>" in svg


# ---------------------------------------------------------------------
# Auth-required endpoints
# ---------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "/api/attendance/today",
    "/api/validation-requests",
    "/api/notifications",
    "/api/notifications/unread-count",
    "/api/census/identify/abc",
    "/api/census/qr/abc",
])
async def test_phase5_get_endpoints_require_bearer_token(
    async_client: AsyncClient, url: str
) -> None:
    response = await async_client.get(url)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_attendance_scan_requires_bearer_token(
    async_client: AsyncClient,
) -> None:
    response = await async_client.post(
        "/api/attendance/scan", json={"qrToken": "abc"}
    )
    assert response.status_code == 401
