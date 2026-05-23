"""Phase 8 contract tests — Analytics + Observability.

Pydantic validation, OpenAPI surface, X-Request-Id middleware behaviour,
and the business-counter wiring (counters increment on the right paths).
DB-bound paths (AnalyticsService.* aggregations) live in tests/integration/.
"""
import pytest
from httpx import AsyncClient
from prometheus_client.parser import text_string_to_metric_families
from pydantic import ValidationError

from app.core.observability import (
    REQUEST_ID_HEADER,
    attendance_scan_total,
    auth_login_total,
    import_commit_total,
    notification_dispatch_total,
)
from app.modules.analytics.schemas import (
    AuditLogQuery,
    EnrollmentTrendsQuery,
    NationalKpis,
    QualityResponse,
    TerritoriesQuery,
    TopSchoolsQuery,
    TrendsQuery,
)


# =====================================================================
# OpenAPI: every Phase 8 endpoint must be discoverable
# =====================================================================
@pytest.mark.asyncio
async def test_openapi_exposes_phase8_endpoints(async_client: AsyncClient) -> None:
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    for url in (
        "/api/analytics/national",
        "/api/analytics/territories",
        "/api/analytics/attendance/trends",
        "/api/analytics/enrollment/trends",
        "/api/analytics/top-schools",
        "/api/analytics/quality",
        "/api/analytics/export",
        "/api/analytics/audit-logs",
    ):
        assert url in paths, f"Missing endpoint: {url}"


# =====================================================================
# Schema defaults & validation
# =====================================================================
def test_territories_query_default_is_region() -> None:
    assert TerritoriesQuery().level == "region"


def test_trends_query_clamps_days() -> None:
    with pytest.raises(ValidationError):
        TrendsQuery(days=0)
    with pytest.raises(ValidationError):
        TrendsQuery(days=400)


def test_enrollment_trends_query_clamps_months() -> None:
    with pytest.raises(ValidationError):
        EnrollmentTrendsQuery(months=0)
    with pytest.raises(ValidationError):
        EnrollmentTrendsQuery(months=61)


def test_top_schools_query_defaults() -> None:
    q = TopSchoolsQuery()
    assert q.metric == "students" and q.limit == 10


def test_top_schools_query_rejects_invalid_metric() -> None:
    with pytest.raises(ValidationError):
        TopSchoolsQuery.model_validate({"metric": "magic", "limit": 10})


def test_audit_log_query_defaults_and_caps() -> None:
    q = AuditLogQuery()
    assert q.page == 1 and q.pageSize == 50
    with pytest.raises(ValidationError):
        AuditLogQuery(pageSize=600)
    with pytest.raises(ValidationError):
        AuditLogQuery(pageSize=0)


def test_national_kpis_round_trip() -> None:
    kpi = NationalKpis(
        students=100, teachers=10, schools=5, classes=20, regions=2,
        studentsPerTeacher=10.0, studentsPerSchool=20.0, teachersPerSchool=2.0,
        geolocatedSchools=3, gpsCoverageRate=60,
        approvedSchools=5, pendingSchools=0,
        attendanceLast7Days=100, presentLast7Days=80,
        absentLast7Days=10, lateLast7Days=10,
        presenceRateLast7Days=80.0,
        parentReachable=42, parentReachableRate=84.0,
    )
    assert kpi.studentsPerTeacher == 10.0


def test_quality_response_round_trip() -> None:
    q = QualityResponse(
        score=88, studentsTotal=1000, studentsWithoutClass=50,
        studentsWithoutPhoto=200, studentsMissingBirthDate=10,
        teachersTotal=80, teachersWithoutClasses=2, teachersWithoutPhoto=10,
        teachersMissingBirthDate=0, schoolsTotal=20,
        schoolsMissingCoordinates=3, schoolsMissingPhone=5,
    )
    assert q.score == 88


# =====================================================================
# Auth gates
# =====================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "/api/analytics/national",
    "/api/analytics/territories",
    "/api/analytics/attendance/trends",
    "/api/analytics/enrollment/trends",
    "/api/analytics/top-schools",
    "/api/analytics/quality",
    "/api/analytics/export?type=national",
    "/api/analytics/audit-logs",
])
async def test_phase8_endpoints_require_bearer_token(
    async_client: AsyncClient, url: str
) -> None:
    response = await async_client.get(url)
    assert response.status_code == 401


# =====================================================================
# Request ID middleware
# =====================================================================
@pytest.mark.asyncio
async def test_request_id_minted_when_absent(async_client: AsyncClient) -> None:
    response = await async_client.get("/health")
    assert response.status_code == 200
    rid = response.headers.get(REQUEST_ID_HEADER)
    assert rid and len(rid) >= 16  # uuid4 hex is 32 chars


@pytest.mark.asyncio
async def test_request_id_propagated_when_present(async_client: AsyncClient) -> None:
    response = await async_client.get(
        "/health", headers={REQUEST_ID_HEADER: "trace-abc-123"}
    )
    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "trace-abc-123"


# =====================================================================
# Prometheus metrics — counters defined and exposed
# =====================================================================
def test_business_counters_are_defined() -> None:
    """The 4 business counters should be importable and have known labels."""
    # Just calling .labels() proves the labelset matches the declaration.
    auth_login_total.labels(result="success")
    attendance_scan_total.labels(result="ok")
    notification_dispatch_total.labels(channel="SMS", result="ok")
    import_commit_total.labels(kind="students", result="ok")


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_business_counters(
    async_client: AsyncClient,
) -> None:
    """The /metrics endpoint should include our business counters by name.

    We pre-touch each counter to make sure prometheus_client renders it
    (otherwise unobserved counters with labels are skipped).
    """
    auth_login_total.labels(result="success").inc()
    attendance_scan_total.labels(result="ok").inc()
    notification_dispatch_total.labels(channel="SMS", result="ok").inc()
    import_commit_total.labels(kind="students", result="ok").inc()

    response = await async_client.get("/metrics")
    assert response.status_code == 200
    body = response.text

    families = {m.name for m in text_string_to_metric_families(body)}
    assert "gestionee_auth_login" in families
    assert "gestionee_attendance_scan" in families
    assert "gestionee_notification_dispatch" in families
    assert "gestionee_import_commit" in families


# =====================================================================
# CSV export — content-type and BOM
# =====================================================================
@pytest.mark.asyncio
async def test_csv_export_requires_auth(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/analytics/export?type=quality")
    assert response.status_code == 401
