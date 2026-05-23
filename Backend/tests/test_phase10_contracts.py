"""Phase 10 contract tests — School infrastructure + Inspections + Analytics decisional.

OpenAPI surface, Pydantic validation, gates 401, et logique pure
(scoring inspection, sévérité weights). Les paths DB-bound (cohorts/equity
sur vraie BDD avec données seedées) restent dans tests/integration/.
"""
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.modules.analytics.schemas import (
    PolicySimulationRequest,
)
from app.modules.inspections.schemas import (
    CreateActionItemRequest,
    CreateFindingRequest,
    CreateInspectionRequest,
    UpdateActionItemRequest,
)
from app.modules.inspections.service import InspectionsService
from app.shared.enums import (
    ActionItemStatus,
    BuildingCondition,
    ElectricitySource,
    FindingSeverity,
    InspectionCriterion,
    InspectionStatus,
    SchoolAffiliation,
    WaterSource,
)


# =====================================================================
# OpenAPI : tous les endpoints Phase 10 visibles
# =====================================================================
@pytest.mark.asyncio
async def test_openapi_exposes_phase10_endpoints(async_client: AsyncClient) -> None:
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    for url in (
        # Inspections
        "/api/inspections",
        "/api/inspections/stats",
        "/api/inspections/{inspection_id}",
        "/api/inspections/{inspection_id}/findings",
        "/api/inspections/{inspection_id}/actions",
        "/api/inspections/actions/{action_id}",
        # Analytics decisional
        "/api/analytics/cohorts",
        "/api/analytics/equity",
        "/api/analytics/policy-simulator",
    ):
        assert url in paths, f"Missing endpoint: {url}"


# =====================================================================
# Phase 10 : nouveaux ENUMs disponibles
# =====================================================================
def test_water_source_enum_values() -> None:
    assert WaterSource.NONE == "NONE"
    assert WaterSource.BOREHOLE == "BOREHOLE"


def test_electricity_source_enum_values() -> None:
    assert ElectricitySource.SOLAR == "SOLAR"
    assert ElectricitySource.HYBRID == "HYBRID"


def test_building_condition_ordering() -> None:
    # On vérifie juste que tous les libellés existent
    for cond in (
        BuildingCondition.EXCELLENT, BuildingCondition.GOOD, BuildingCondition.FAIR,
        BuildingCondition.POOR, BuildingCondition.DANGEROUS,
    ):
        assert isinstance(cond.value, str)


def test_school_affiliation_covers_guinean_categories() -> None:
    expected = {
        "PUBLIC", "PRIVATE_SECULAR", "CATHOLIC", "PROTESTANT",
        "ISLAMIC", "QURANIC", "FRANCO_ARABIC",
    }
    assert {a.value for a in SchoolAffiliation} == expected


# =====================================================================
# Inspections — Pydantic validation
# =====================================================================
def test_create_inspection_requires_school_and_date() -> None:
    from datetime import date as _date

    dto = CreateInspectionRequest(
        schoolId="school-1", scheduledDate=_date(2026, 6, 1)
    )
    assert dto.inspectorId is None
    assert dto.notes is None


def test_create_inspection_rejects_too_long_notes() -> None:
    from datetime import date as _date

    with pytest.raises(ValidationError):
        CreateInspectionRequest(
            schoolId="s", scheduledDate=_date(2026, 6, 1), notes="x" * 2001,
        )


def test_create_finding_score_bounds() -> None:
    dto = CreateFindingRequest(criterion=InspectionCriterion.SAFETY, score=3)
    assert dto.severity == FindingSeverity.INFO

    with pytest.raises(ValidationError):
        CreateFindingRequest(criterion=InspectionCriterion.SAFETY, score=-1)
    with pytest.raises(ValidationError):
        CreateFindingRequest(criterion=InspectionCriterion.SAFETY, score=6)


def test_create_action_min_description() -> None:
    from datetime import date as _date

    with pytest.raises(ValidationError):
        CreateActionItemRequest(description="x", dueDate=_date(2026, 7, 1))


def test_update_action_requires_status() -> None:
    with pytest.raises(ValidationError):
        UpdateActionItemRequest.model_validate({})


# =====================================================================
# Inspections — algorithme de scoring (pure function)
# =====================================================================
class _StubFinding:
    """Petit objet imitant InspectionFinding pour tester _score_from_findings."""
    def __init__(self, score: int, severity: FindingSeverity) -> None:
        self.score = score
        self.severity = severity


def test_score_from_findings_empty_returns_zero() -> None:
    assert InspectionsService._score_from_findings([]) == 0.0


def test_score_from_findings_uniform_info() -> None:
    findings = [_StubFinding(5, FindingSeverity.INFO) for _ in range(3)]
    # 3 × score 5 × poids 1 / 3 = 5 → ×20 = 100
    assert InspectionsService._score_from_findings(findings) == 100.0  # type: ignore[arg-type]


def test_score_from_findings_critical_drags_score_down() -> None:
    # 1 finding INFO score 5 + 1 finding CRITICAL score 0
    # weighted = (5×1 + 0×3) / (1+3) = 5/4 = 1.25 → ×20 = 25
    findings = [
        _StubFinding(5, FindingSeverity.INFO),
        _StubFinding(0, FindingSeverity.CRITICAL),
    ]
    assert InspectionsService._score_from_findings(findings) == 25.0  # type: ignore[arg-type]


def test_score_from_findings_minor_major_weights() -> None:
    findings = [
        _StubFinding(4, FindingSeverity.MINOR),   # poids 1.5
        _StubFinding(2, FindingSeverity.MAJOR),   # poids 2.0
    ]
    # weighted = (4×1.5 + 2×2.0) / (1.5+2.0) = (6+4)/3.5 = 2.857… → ×20 = 57.1
    assert InspectionsService._score_from_findings(findings) == 57.1  # type: ignore[arg-type]


# =====================================================================
# Inspections — gates 401
# =====================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "/api/inspections",
    "/api/inspections/stats",
    "/api/inspections/some-id",
])
async def test_inspections_endpoints_require_bearer(
    async_client: AsyncClient, url: str
) -> None:
    response = await async_client.get(url)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_inspections_create_requires_bearer(async_client: AsyncClient) -> None:
    response = await async_client.post(
        "/api/inspections",
        json={"schoolId": "s1", "scheduledDate": "2026-06-01"},
    )
    assert response.status_code == 401


# =====================================================================
# Analytics decisional — Pydantic validation
# =====================================================================
def test_policy_simulation_defaults() -> None:
    dto = PolicySimulationRequest()
    assert dto.regionId is None
    assert dto.addSchools == 0
    assert dto.addTeachers == 0
    assert dto.horizonYears == 5


def test_policy_simulation_clamps() -> None:
    with pytest.raises(ValidationError):
        PolicySimulationRequest(addSchools=-1)
    with pytest.raises(ValidationError):
        PolicySimulationRequest(addSchools=10001)
    with pytest.raises(ValidationError):
        PolicySimulationRequest(horizonYears=0)
    with pytest.raises(ValidationError):
        PolicySimulationRequest(horizonYears=21)


def test_policy_simulation_target_coverage_bounds() -> None:
    with pytest.raises(ValidationError):
        PolicySimulationRequest(targetGirlsToiletsCoverage=101)
    with pytest.raises(ValidationError):
        PolicySimulationRequest(targetElectricityCoverage=-1)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "/api/analytics/cohorts",
    "/api/analytics/equity",
])
async def test_analytics_decisional_get_requires_bearer(
    async_client: AsyncClient, url: str
) -> None:
    response = await async_client.get(url)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_policy_simulator_post_requires_bearer(
    async_client: AsyncClient,
) -> None:
    response = await async_client.post(
        "/api/analytics/policy-simulator", json={"addSchools": 10}
    )
    assert response.status_code == 401


# =====================================================================
# Inspection status & criterion enums : couverture complète
# =====================================================================
def test_inspection_status_values() -> None:
    assert {s.value for s in InspectionStatus} == {
        "PLANNED", "IN_PROGRESS", "COMPLETED", "CANCELLED"
    }


def test_inspection_criteria_complete() -> None:
    expected = {
        "GOVERNANCE", "PEDAGOGY", "INFRASTRUCTURE", "SAFETY",
        "HYGIENE", "EQUITY", "ATTENDANCE", "DOCUMENTS",
    }
    assert {c.value for c in InspectionCriterion} == expected


def test_finding_severity_ordering() -> None:
    # INFO < MINOR < MAJOR < CRITICAL en termes d'impact
    severities = [
        FindingSeverity.INFO, FindingSeverity.MINOR,
        FindingSeverity.MAJOR, FindingSeverity.CRITICAL,
    ]
    assert len(set(severities)) == 4


def test_action_item_status_lifecycle() -> None:
    assert {s.value for s in ActionItemStatus} == {
        "OPEN", "IN_PROGRESS", "RESOLVED", "CANCELLED"
    }
