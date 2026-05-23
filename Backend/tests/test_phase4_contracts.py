"""Phase 4 contract tests — Academics + Reports.

Pydantic validation + OpenAPI surface only. End-to-end tests with seed data
will live in tests/integration/ in a later phase.
"""
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.modules.academics.schemas import (
    CreateAssessmentRequest,
    CreateParentRequest,
    CreateSchoolYearRequest,
    CreateSubjectRequest,
    GenerateReportCardsRequest,
    GradeInput,
    ParentStudentLink,
    SaveGradesRequest,
    UpdateValidationStatusRequest,
)
from app.modules.reports.schemas import (
    BulletinVerifyResponse,
    GenerateBulletinsRequest,
)
from app.shared.enums import (
    AcademicPeriodType,
    AcademicValidationStatus,
    AssessmentType,
    ParentRelationType,
)


# ---------------------------------------------------------------------
# OpenAPI: every Phase 4 endpoint must be discoverable
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_openapi_exposes_phase4_endpoints(async_client: AsyncClient) -> None:
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    for url in (
        # Academics
        "/api/academics/parents",
        "/api/academics/parents/{parent_id}",
        "/api/academics/school-years",
        "/api/academics/subjects",
        "/api/academics/assessments",
        "/api/academics/assessments/{assessment_id}/status",
        "/api/academics/grades",
        "/api/academics/grades/bulk",
        "/api/academics/report-cards",
        "/api/academics/report-cards/generate",
        "/api/academics/report-cards/{report_card_id}/status",
        # Reports
        "/api/reports/bulletins/verify/{verification_code}",
        "/api/reports/bulletins/{report_card_id}/pdf",
        "/api/reports/bulletins/generate-batch",
    ):
        assert url in paths, f"Missing endpoint: {url}"


# ---------------------------------------------------------------------
# Pydantic — Academics validation
# ---------------------------------------------------------------------
def test_create_parent_requires_at_least_one_link() -> None:
    with pytest.raises(ValidationError):
        CreateParentRequest(
            firstName="Aïssata", lastName="Camara", phone="620000000",
            links=[],
        )


def test_create_parent_strips_and_validates_email() -> None:
    dto = CreateParentRequest(
        firstName="  Aïssata  ", lastName="Camara", phone="620000000",
        email="aissata@example.com",
        links=[ParentStudentLink(studentId="s1", relation=ParentRelationType.MOTHER)],
    )
    assert dto.firstName == "Aïssata"
    assert dto.email == "aissata@example.com"


def test_create_parent_min_phone_length() -> None:
    with pytest.raises(ValidationError):
        CreateParentRequest(
            firstName="Test", lastName="Test", phone="123",
            links=[ParentStudentLink(studentId="s1", relation=ParentRelationType.FATHER)],
        )


def test_create_school_year_default_period_type() -> None:
    from datetime import date

    dto = CreateSchoolYearRequest(
        name="2026-2027", startDate=date(2026, 9, 1), endDate=date(2027, 6, 30),
    )
    assert dto.periodType == AcademicPeriodType.TRIMESTER
    assert dto.isActive is False


def test_create_subject_min_lengths_and_coefficient() -> None:
    dto = CreateSubjectRequest(code="MAT", name="Mathématiques", coefficient=4)
    assert dto.coefficient == 4

    with pytest.raises(ValidationError):
        CreateSubjectRequest(code="X", name="Test")
    with pytest.raises(ValidationError):
        CreateSubjectRequest(code="MAT", name="Test", coefficient=0)


def test_create_assessment_required_fields() -> None:
    dto = CreateAssessmentRequest(
        title="Composition Maths", type=AssessmentType.COMPOSITION,
        schoolYearId="y1", periodId="p1", subjectId="sub1", classRoomId="c1",
    )
    assert dto.coefficient is None  # service falls back to subject coef
    assert dto.maxScore is None  # service defaults to 20

    with pytest.raises(ValidationError):
        CreateAssessmentRequest.model_validate({  # type: ignore[call-arg]
            "title": "Test", "type": "COMPOSITION", "schoolYearId": "y1",
            "periodId": "p1", "subjectId": "sub1",
            # classRoomId missing
        })


def test_grade_input_rejects_negative_score() -> None:
    GradeInput(studentId="s1", score=15.5)
    with pytest.raises(ValidationError):
        GradeInput(studentId="s1", score=-1)


def test_save_grades_requires_at_least_one() -> None:
    with pytest.raises(ValidationError):
        SaveGradesRequest(assessmentId="a1", grades=[])


def test_generate_report_cards_required() -> None:
    dto = GenerateReportCardsRequest(schoolYearId="y1", periodId="p1")
    assert dto.classRoomId is None


def test_update_validation_status_rejects_invalid() -> None:
    UpdateValidationStatusRequest(status=AcademicValidationStatus.VALIDATED)
    with pytest.raises(ValidationError):
        UpdateValidationStatusRequest.model_validate({"status": "MAYBE"})


# ---------------------------------------------------------------------
# Pydantic — Reports validation
# ---------------------------------------------------------------------
def test_generate_bulletins_request_optional_class_and_ids() -> None:
    dto = GenerateBulletinsRequest(schoolYearId="y1", periodId="p1")
    assert dto.classRoomId is None and dto.reportCardIds is None


def test_bulletin_verify_response_default_invalid() -> None:
    resp = BulletinVerifyResponse(verificationCode="UNKNOWN-CODE", valid=False)
    assert resp.studentFullName is None
    assert resp.average is None


# ---------------------------------------------------------------------
# Auth-required endpoints
# ---------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "/api/academics/parents",
    "/api/academics/school-years",
    "/api/academics/subjects",
    "/api/academics/assessments",
    "/api/academics/grades",
    "/api/academics/report-cards",
])
async def test_academics_endpoints_require_bearer_token(
    async_client: AsyncClient, url: str
) -> None:
    response = await async_client.get(url)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_pdf_render_requires_bearer_token(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/reports/bulletins/abc/pdf")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_verify_endpoint_is_public(async_client: AsyncClient) -> None:
    """The verify endpoint should NOT require auth (anyone with a QR code)."""
    response = await async_client.get("/api/reports/bulletins/verify/UNKNOWN-CODE")
    # Without DB this will likely fail at runtime, but we expect either:
    # - 200 with valid=False (DB available, code unknown), OR
    # - 500 (no DB) — but NEVER 401
    assert response.status_code != 401
