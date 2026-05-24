"""Module 3C — Score composite de priorité d'investissement par école.

Couvre :

1.  test_score_infrastructure_full_amenities_returns_low_score
2.  test_score_infrastructure_missing_water_electricity_high_score
3.  test_score_saturation_critical_returns_25
4.  test_score_equity_gpi_critical_returns_25
5.  test_score_accessibility_rural_returns_15
6.  test_classify_thresholds_table_driven
7.  test_compute_priority_scores_persists_for_all_schools
8.  test_compute_requires_admin
9.  test_top_priorities_returns_sorted_desc
10. test_list_filters_by_category
11. test_list_respects_territorial_scope
12. test_breakdown_json_stores_per_dimension_details
13. test_cockpit_kpis_includes_high_investment_priority (hook Module 19)
14. test_cartography_layer_investment_priority_returns_geojson (hook Module 3A)
15. test_recompute_upserts_old_score

Les tests purs (1-6) ne touchent pas la DB. Les tests 7+ utilisent
``db_session`` + factories.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError
from app.modules.academics.models import SchoolYear
from app.modules.auth.models import User
from app.modules.enrollment.enums import (
    EnrollmentClassLevel,
    EnrollmentSource,
)
from app.modules.enrollment.models import Enrollment
from app.modules.investment.enums import PriorityCategory
from app.modules.investment.models import InvestmentPriorityScore
from app.modules.investment.scoring import (
    classify,
    compute_total,
    score_accessibility,
    score_equity,
    score_infrastructure,
    score_saturation,
)
from app.modules.investment.service import InvestmentService
from app.modules.projections.enums import (
    BASELINE_SCENARIO_ID,
    CapacityScope,
    CapacitySeverity,
)
from app.modules.projections.models import (
    CapacityDemandSnapshot,
    ProjectionScenario,
)
from app.shared.base import generate_cuid
from app.shared.enums import (
    AcademicPeriodType,
    BuildingCondition,
    ElectricitySource,
    Gender,
    UserRole,
    ValidationStatus,
    WaterSource,
    ZoneType,
)
from tests.integration import factories

pytestmark = pytest.mark.integration


# ===========================================================================
# Helpers
# ===========================================================================
async def _make_school_year(
    session: AsyncSession,
    *,
    year: int = 2025,
) -> SchoolYear:
    sy = SchoolYear(
        id=generate_cuid(),
        name=f"YEAR-3C-{generate_cuid()[:6]}",
        startDate=datetime(year, 9, 1, tzinfo=UTC),
        endDate=datetime(year + 1, 6, 30, tzinfo=UTC),
        periodType=AcademicPeriodType.TRIMESTER,
        isActive=True,
    )
    session.add(sy)
    await session.flush()
    return sy


async def _make_user(
    session: AsyncSession,
    role: UserRole = UserRole.NATIONAL_ADMIN,
    **kwargs: Any,
) -> User:
    uid = generate_cuid()
    user = User(
        id=uid,
        email=f"{role.value.lower()}-{uid[:6]}@test.local",
        passwordHash="x",
        fullName=f"Test {role.value}",
        role=role,
        isActive=True,
        **kwargs,
    )
    session.add(user)
    await session.flush()
    return user


async def _ensure_baseline_scenario(session: AsyncSession) -> None:
    existing = await session.get(ProjectionScenario, BASELINE_SCENARIO_ID)
    if existing is None:
        session.add(
            ProjectionScenario(
                id=BASELINE_SCENARIO_ID,
                name="BASELINE",
                description="Scénario par défaut (seed test).",
                demographicGrowthRate=Decimal("0.0240"),
                createdAt=datetime.now(UTC),
            )
        )
        await session.flush()


# ===========================================================================
# 1. score_infrastructure full amenities returns low score
# ===========================================================================
def test_score_infrastructure_full_amenities_returns_low_score() -> None:
    data = {
        "waterSource": WaterSource.NETWORK,
        "electricitySource": ElectricitySource.GRID,
        "toiletsBoys": 5,
        "toiletsGirls": 5,
        "classroomsTotal": 10,
        "classroomsUsable": 10,
        "buildingCondition": BuildingCondition.EXCELLENT,
        "internetAvailable": True,
    }
    score, details = score_infrastructure(data)
    assert score == 0
    assert details["missingWater"] is False
    assert details["missingElectricity"] is False
    assert details["missingToilets"] is False
    assert details["buildingPoints"] == 0
    assert details["missingInternet"] is False


# ===========================================================================
# 2. score_infrastructure missing water + electricity high score
# ===========================================================================
def test_score_infrastructure_missing_water_electricity_high_score() -> None:
    data = {
        "waterSource": WaterSource.NONE,
        "electricitySource": ElectricitySource.NONE,
        "toiletsBoys": 0,
        "toiletsGirls": 0,
        "classroomsTotal": 10,
        "classroomsUsable": 3,  # ratio 0.3 < 0.5
        "buildingCondition": BuildingCondition.POOR,
        "internetAvailable": False,
    }
    score, details = score_infrastructure(data)
    # 10 (water) + 10 (elec) + 10 (toilets) + 15 (POOR) + 15 (ratio<0.5)
    # + 5 (no internet) = 65 → cappé à 35.
    assert score == 35
    assert details["missingWater"] is True
    assert details["missingElectricity"] is True
    assert details["missingToilets"] is True
    assert details["classroomsRatioCritical"] is True
    assert details["rawPoints"] >= 35


# ===========================================================================
# 3. score_saturation CRITICAL returns 25
# ===========================================================================
def test_score_saturation_critical_returns_25() -> None:
    s, details = score_saturation(CapacitySeverity.CRITICAL)
    assert s == 25
    assert details["severity"] == "CRITICAL"

    s_warn, _ = score_saturation(CapacitySeverity.WARNING)
    assert s_warn == 15

    s_ok, _ = score_saturation(CapacitySeverity.OK)
    assert s_ok == 0

    s_none, details_none = score_saturation(None)
    assert s_none == 0
    assert details_none["severity"] is None


# ===========================================================================
# 4. score_equity GPI CRITICAL returns 25
# ===========================================================================
def test_score_equity_gpi_critical_returns_25() -> None:
    s_crit, details_crit = score_equity(Decimal("0.70"))
    assert s_crit == 25
    assert details_crit["severity"] == "CRITICAL"

    s_warn, details_warn = score_equity(Decimal("0.90"))
    assert s_warn == 15
    assert details_warn["severity"] == "WARNING"

    s_normal, details_normal = score_equity(Decimal("1.00"))
    assert s_normal == 0
    assert details_normal["severity"] == "NORMAL"

    s_none, details_none = score_equity(None)
    assert s_none == 0
    assert details_none["severity"] == "UNKNOWN"


# ===========================================================================
# 5. score_accessibility RURAL returns 15
# ===========================================================================
def test_score_accessibility_rural_returns_15() -> None:
    s, details = score_accessibility(ZoneType.RURAL)
    assert s == 15
    assert details["zonePoints"] == 15
    assert details["distanceBonus"] == 0

    s_peri, _ = score_accessibility(ZoneType.PERI_URBAN)
    assert s_peri == 8

    s_urban, _ = score_accessibility(ZoneType.URBAN)
    assert s_urban == 0

    # Bonus distance.
    s_rural_far, details_far = score_accessibility(ZoneType.RURAL, 5.0)
    assert s_rural_far == 20
    assert details_far["distanceBonus"] == 5


# ===========================================================================
# 6. classify thresholds table-driven
# ===========================================================================
@pytest.mark.parametrize(
    "total,expected",
    [
        (0, PriorityCategory.BASSE),
        (29, PriorityCategory.BASSE),
        (30, PriorityCategory.MOYENNE),
        (49, PriorityCategory.MOYENNE),
        (50, PriorityCategory.HAUTE),
        (69, PriorityCategory.HAUTE),
        (70, PriorityCategory.TRES_HAUTE),
        (100, PriorityCategory.TRES_HAUTE),
    ],
)
def test_classify_thresholds_table_driven(
    total: int, expected: PriorityCategory,
) -> None:
    assert classify(total) == expected


def test_compute_total_caps_at_100() -> None:
    """Garde-fou : la somme ne doit pas dépasser 100."""
    assert compute_total([35, 25, 25, 20]) == 100  # déjà 105 brut → cap 100
    assert compute_total([10, 5, 5, 0]) == 20


# ===========================================================================
# 7-12-15. Fixture commune
# ===========================================================================
@pytest_asyncio.fixture(loop_scope="session")
async def inv_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Setup réutilisable : 3 écoles aux profils contrastés + 1 année."""
    factories.bind(db_session)
    region = await factories.RegionFactory.create_async()
    other_region = await factories.RegionFactory.create_async()
    pref = await factories.PrefectureFactory.create_async(regionId=region.id)
    sub_pref_rural = await factories.SubPrefectureFactory.create_async(
        regionId=region.id,
        prefectureId=pref.id,
        defaultZoneType=ZoneType.RURAL,
    )
    sub_pref_urban = await factories.SubPrefectureFactory.create_async(
        regionId=region.id,
        prefectureId=pref.id,
        defaultZoneType=ZoneType.URBAN,
    )
    # École A : RURAL + manque tout → score élevé.
    school_a = await factories.SchoolFactory.create_async(
        regionId=region.id,
        prefectureId=pref.id,
        subPrefectureId=sub_pref_rural.id,
        latitude=9.5,
        longitude=-13.5,
        waterSource=WaterSource.NONE,
        electricitySource=ElectricitySource.NONE,
        toiletsBoys=0,
        toiletsGirls=0,
        classroomsTotal=10,
        classroomsUsable=3,
        buildingCondition=BuildingCondition.POOR,
        internetAvailable=False,
        status=ValidationStatus.APPROVED,
    )
    # École B : URBAIN bien équipée → score bas.
    school_b = await factories.SchoolFactory.create_async(
        regionId=region.id,
        prefectureId=pref.id,
        subPrefectureId=sub_pref_urban.id,
        latitude=9.6,
        longitude=-13.6,
        waterSource=WaterSource.NETWORK,
        electricitySource=ElectricitySource.GRID,
        toiletsBoys=5,
        toiletsGirls=5,
        classroomsTotal=10,
        classroomsUsable=10,
        buildingCondition=BuildingCondition.EXCELLENT,
        internetAvailable=True,
        status=ValidationStatus.APPROVED,
    )
    # École C : autre région — pour tester le filtre territorial.
    school_c = await factories.SchoolFactory.create_async(
        regionId=other_region.id,
        prefectureId=None,
        subPrefectureId=None,
        latitude=10.0,
        longitude=-14.0,
        waterSource=WaterSource.WELL,
        electricitySource=ElectricitySource.SOLAR,
        toiletsBoys=2,
        toiletsGirls=2,
        classroomsTotal=8,
        classroomsUsable=6,
        buildingCondition=BuildingCondition.FAIR,
        internetAvailable=False,
        status=ValidationStatus.APPROVED,
    )

    year = await _make_school_year(db_session)

    # Enrollment pour école A : GPI très bas (filles=10, garçons=50 → 0.20).
    for gender, count in ((Gender.FEMALE, 10), (Gender.MALE, 50)):
        db_session.add(
            Enrollment(
                id=generate_cuid(),
                schoolYearId=year.id,
                schoolId=school_a.id,
                classLevel=EnrollmentClassLevel.CP1,
                gender=gender,
                count=count,
                source=EnrollmentSource.CENSUS_DECLARED,
                recordedAt=datetime.now(UTC),
            )
        )
    # Enrollment pour école B : GPI normal (filles=50, garçons=50 → 1.0).
    for gender, count in ((Gender.FEMALE, 50), (Gender.MALE, 50)):
        db_session.add(
            Enrollment(
                id=generate_cuid(),
                schoolYearId=year.id,
                schoolId=school_b.id,
                classLevel=EnrollmentClassLevel.CP1,
                gender=gender,
                count=count,
                source=EnrollmentSource.CENSUS_DECLARED,
                recordedAt=datetime.now(UTC),
            )
        )
    await db_session.flush()

    # Capacity snapshot CRITICAL pour A (horizon +1).
    await _ensure_baseline_scenario(db_session)
    db_session.add(
        CapacityDemandSnapshot(
            id=generate_cuid(),
            baseSchoolYearId=year.id,
            projectedYear=2027,
            scope=CapacityScope.SCHOOL,
            entityId=school_a.id,
            capacity=100,
            demand=200,
            gap=100,
            saturationPct=Decimal("200.00"),
            severity=CapacitySeverity.CRITICAL,
            scenarioId=BASELINE_SCENARIO_ID,
            computedAt=datetime.now(UTC),
        )
    )
    db_session.add(
        CapacityDemandSnapshot(
            id=generate_cuid(),
            baseSchoolYearId=year.id,
            projectedYear=2027,
            scope=CapacityScope.SCHOOL,
            entityId=school_b.id,
            capacity=500,
            demand=300,
            gap=-200,
            saturationPct=Decimal("60.00"),
            severity=CapacitySeverity.OK,
            scenarioId=BASELINE_SCENARIO_ID,
            computedAt=datetime.now(UTC),
        )
    )
    await db_session.flush()

    admin = await _make_user(db_session, role=UserRole.NATIONAL_ADMIN)
    regional = await _make_user(
        db_session,
        role=UserRole.REGIONAL_ADMIN,
        regionId=region.id,
    )
    teacher = await _make_user(db_session, role=UserRole.TEACHER)

    return {
        "region": region,
        "other_region": other_region,
        "school_a": school_a,
        "school_b": school_b,
        "school_c": school_c,
        "year": year,
        "admin": admin,
        "regional": regional,
        "teacher": teacher,
    }


# ===========================================================================
# 7. compute_priority_scores persists for all schools
# ===========================================================================
async def test_compute_priority_scores_persists_for_all_schools(
    db_session: AsyncSession,
    inv_ctx: dict[str, Any],
) -> None:
    svc = InvestmentService(db_session)
    response = await svc.compute_priority_scores(
        inv_ctx["year"].id, inv_ctx["admin"],
    )
    # 3 écoles APPROVED → 3 scores.
    assert response.scoresComputed == 3
    rows = (
        await db_session.execute(select(InvestmentPriorityScore))
    ).scalars().all()
    assert len(rows) == 3

    # École A doit être TRES_HAUTE (infra dégradée + sat CRITICAL + GPI 0.20 + RURAL).
    by_school = {r.schoolId: r for r in rows}
    score_a = by_school[inv_ctx["school_a"].id]
    # Infra=35 + Sat=25 + Equity=25 + Access=15 = 100, capé à 100.
    assert score_a.totalScore >= 70
    assert score_a.priorityCategory == PriorityCategory.TRES_HAUTE

    # École B : urbaine bien équipée → BASSE.
    score_b = by_school[inv_ctx["school_b"].id]
    assert score_b.totalScore < 30
    assert score_b.priorityCategory == PriorityCategory.BASSE

    # byCategory contient les 4 clefs.
    assert set(response.byCategory.keys()) == {
        "TRES_HAUTE", "HAUTE", "MOYENNE", "BASSE",
    }


# ===========================================================================
# 8. compute requires admin
# ===========================================================================
async def test_compute_requires_admin(
    db_session: AsyncSession,
    inv_ctx: dict[str, Any],
) -> None:
    svc = InvestmentService(db_session)
    # TEACHER refusé.
    with pytest.raises(ForbiddenError):
        await svc.compute_priority_scores(
            inv_ctx["year"].id, inv_ctx["teacher"],
        )
    # REGIONAL_ADMIN refusé (calcul réservé NATIONAL/MINISTRY).
    with pytest.raises(ForbiddenError):
        await svc.compute_priority_scores(
            inv_ctx["year"].id, inv_ctx["regional"],
        )


# ===========================================================================
# 9. top_priorities returns sorted desc
# ===========================================================================
async def test_top_priorities_returns_sorted_desc(
    db_session: AsyncSession,
    inv_ctx: dict[str, Any],
) -> None:
    svc = InvestmentService(db_session)
    await svc.compute_priority_scores(inv_ctx["year"].id, inv_ctx["admin"])
    top = await svc.top_priorities(inv_ctx["admin"], limit=10)
    assert len(top) >= 2
    scores = [t.totalScore for t in top]
    # Vérifie tri décroissant.
    assert scores == sorted(scores, reverse=True)
    # École A doit apparaître en tête (score le plus haut).
    assert top[0].schoolId == inv_ctx["school_a"].id


# ===========================================================================
# 10. list filters by category
# ===========================================================================
async def test_list_filters_by_category(
    db_session: AsyncSession,
    inv_ctx: dict[str, Any],
) -> None:
    svc = InvestmentService(db_session)
    await svc.compute_priority_scores(inv_ctx["year"].id, inv_ctx["admin"])

    tres_haute = await svc.list_priorities(
        inv_ctx["admin"],
        category=PriorityCategory.TRES_HAUTE,
    )
    # École A est TRES_HAUTE.
    assert any(p.schoolId == inv_ctx["school_a"].id for p in tres_haute)
    for p in tres_haute:
        assert p.priorityCategory == PriorityCategory.TRES_HAUTE

    basse = await svc.list_priorities(
        inv_ctx["admin"],
        category=PriorityCategory.BASSE,
    )
    # École B est BASSE.
    assert any(p.schoolId == inv_ctx["school_b"].id for p in basse)


# ===========================================================================
# 11. list respects territorial scope
# ===========================================================================
async def test_list_respects_territorial_scope(
    db_session: AsyncSession,
    inv_ctx: dict[str, Any],
) -> None:
    svc = InvestmentService(db_session)
    await svc.compute_priority_scores(inv_ctx["year"].id, inv_ctx["admin"])

    # Admin national voit tout.
    all_admin = await svc.list_priorities(inv_ctx["admin"])
    school_ids_admin = {p.schoolId for p in all_admin}
    assert inv_ctx["school_a"].id in school_ids_admin
    assert inv_ctx["school_b"].id in school_ids_admin
    assert inv_ctx["school_c"].id in school_ids_admin

    # Regional admin voit seulement sa région (A + B).
    regional_view = await svc.list_priorities(inv_ctx["regional"])
    region_school_ids = {p.schoolId for p in regional_view}
    assert inv_ctx["school_a"].id in region_school_ids
    assert inv_ctx["school_b"].id in region_school_ids
    assert inv_ctx["school_c"].id not in region_school_ids


# ===========================================================================
# 12. breakdownJson stores per-dimension details
# ===========================================================================
async def test_breakdown_json_stores_per_dimension_details(
    db_session: AsyncSession,
    inv_ctx: dict[str, Any],
) -> None:
    svc = InvestmentService(db_session)
    await svc.compute_priority_scores(inv_ctx["year"].id, inv_ctx["admin"])
    score_a = (
        await db_session.execute(
            select(InvestmentPriorityScore).where(
                InvestmentPriorityScore.schoolId == inv_ctx["school_a"].id,
            )
        )
    ).scalars().one()
    bd = score_a.breakdownJson
    assert bd is not None
    assert "infrastructure" in bd
    assert "saturation" in bd
    assert "equity" in bd
    assert "accessibility" in bd
    # Infra école A — vérifie quelques drapeaux clefs.
    infra = bd["infrastructure"]
    assert infra["missingWater"] is True
    assert infra["missingElectricity"] is True
    assert infra["classroomsRatioCritical"] is True
    # Saturation CRITICAL.
    assert bd["saturation"]["severity"] == "CRITICAL"
    # Equity CRITICAL (GPI=0.20).
    assert bd["equity"]["severity"] == "CRITICAL"
    # Accessibility RURAL.
    assert bd["accessibility"]["zoneType"] == "RURAL"


# ===========================================================================
# 13. cockpit KPIs includes high investment priority (hook Module 19)
# ===========================================================================
async def test_cockpit_kpis_includes_high_investment_priority(
    db_session: AsyncSession,
    inv_ctx: dict[str, Any],
) -> None:
    from app.modules.cockpit.service import CockpitService

    inv_svc = InvestmentService(db_session)
    await inv_svc.compute_priority_scores(
        inv_ctx["year"].id, inv_ctx["admin"],
    )

    cockpit_svc = CockpitService(db_session)
    # Appel direct du compute helper (évite cache Redis pour le test).
    high_count = await cockpit_svc._count_high_investment_priority()
    assert isinstance(high_count, int)
    # École A est TRES_HAUTE → high_count >= 1.
    assert high_count >= 1


# ===========================================================================
# 14. cartography layer investment-priority returns geojson (hook 3A)
# ===========================================================================
async def test_cartography_layer_investment_priority_returns_geojson(
    client: AsyncClient,
    db_session: AsyncSession,
    inv_ctx: dict[str, Any],
    auth_headers: Any,
) -> None:
    inv_svc = InvestmentService(db_session)
    await inv_svc.compute_priority_scores(
        inv_ctx["year"].id, inv_ctx["admin"],
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    r = await client.get(
        "/api/cartography/layers/investment-priority", headers=headers,
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["type"] == "FeatureCollection"
    assert "features" in payload
    # Au moins 1 feature (école A est géolocalisée).
    school_ids_in_layer = {
        f["properties"]["schoolId"] for f in payload["features"]
    }
    assert inv_ctx["school_a"].id in school_ids_in_layer
    # Propriétés clefs présentes.
    sample = next(
        f for f in payload["features"]
        if f["properties"]["schoolId"] == inv_ctx["school_a"].id
    )
    props = sample["properties"]
    assert "priorityCategory" in props
    assert "totalScore" in props
    assert "infrastructureScore" in props
    assert props["priorityCategory"] in {
        "TRES_HAUTE", "HAUTE", "MOYENNE", "BASSE",
    }


# ===========================================================================
# 15. recompute upserts old score (idempotent)
# ===========================================================================
async def test_recompute_upserts_old_score(
    db_session: AsyncSession,
    inv_ctx: dict[str, Any],
) -> None:
    svc = InvestmentService(db_session)
    first = await svc.compute_priority_scores(
        inv_ctx["year"].id, inv_ctx["admin"],
    )
    first_count = first.scoresComputed
    rows_first = (
        await db_session.execute(select(InvestmentPriorityScore))
    ).scalars().all()
    first_ids = {r.id for r in rows_first}
    first_school_ids = {r.schoolId for r in rows_first}

    # Recompute — doit remplacer (pas dupliquer).
    second = await svc.compute_priority_scores(
        inv_ctx["year"].id, inv_ctx["admin"],
    )
    assert second.scoresComputed == first_count
    rows_second = (
        await db_session.execute(select(InvestmentPriorityScore))
    ).scalars().all()
    assert len(rows_second) == len(rows_first)
    # Les schoolIds sont identiques mais les row ids sont neufs (delete+insert).
    assert {r.schoolId for r in rows_second} == first_school_ids
    assert {r.id for r in rows_second}.isdisjoint(first_ids)
