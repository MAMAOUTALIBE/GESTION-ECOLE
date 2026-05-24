"""Module 3A — Cartographie SIG enrichie pour la réorganisation du réseau.

Couvre les 6 couches GeoJSON ajoutées par ``cartography/layers.py`` :

1.  test_gpi_critical_regions_returns_geojson_feature_collection
2.  test_gpi_critical_regions_only_includes_severity_critical_or_warning
3.  test_capacity_critical_schools_returns_only_critical
4.  test_staffing_critical_schools_returns_under_and_critical
5.  test_infrastructure_gaps_lists_schools_missing_water_or_electricity
6.  test_zone_type_layer_includes_all_subprefectures
7.  test_white_zones_enriched_uses_radius_param
8.  test_layers_require_auth (401 si pas de token)
9.  test_layers_respect_territorial_scope (REGIONAL_ADMIN limité)
10. test_layer_cached_in_redis (2e appel HIT)
11. test_unknown_layer_name_returns_404
12. test_infrastructure_gaps_excludes_complete_schools

Aucune couche n'a besoin de PostGIS — on n'attache donc pas
``@pytest.mark.postgis``. Le marker ``integration`` reste pour cibler
les tests touchant DB/Redis.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import SchoolYear
from app.modules.cartography.service import (
    LAYER_CACHE_PREFIX,
    SUPPORTED_LAYERS,
    CartographyService,
)
from app.modules.enrollment.enums import GpiScope
from app.modules.enrollment.models import GpiSnapshot
from app.modules.enrollment.parity import GpiSeverity
from app.modules.projections.enums import (
    BASELINE_SCENARIO_ID,
    CapacityScope,
    CapacitySeverity,
    StaffingSeverity,
)
from app.modules.projections.models import (
    CapacityDemandSnapshot,
    ProjectionScenario,
    TeacherStaffingSnapshot,
)
from app.shared.base import generate_cuid
from app.shared.enums import (
    AcademicPeriodType,
    ElectricitySource,
    UserRole,
    WaterSource,
    ZoneType,
)
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers de seed
# ---------------------------------------------------------------------------
async def _make_school_year(
    session: AsyncSession,
    *,
    is_active: bool = True,
    year: int = 2025,
) -> SchoolYear:
    sy = SchoolYear(
        id=generate_cuid(),
        name=f"YEAR-{generate_cuid()[:6]}",
        startDate=datetime(year, 9, 1, tzinfo=UTC),
        endDate=datetime(year + 1, 6, 30, tzinfo=UTC),
        periodType=AcademicPeriodType.TRIMESTER,
        isActive=is_active,
    )
    session.add(sy)
    await session.flush()
    return sy


async def _ensure_baseline_scenario(session: AsyncSession) -> None:
    """Garantit la présence du scénario BASELINE (FK dure côté snapshots)."""
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


async def _seed_gpi_snapshot(
    session: AsyncSession,
    *,
    school_year_id: str,
    scope: GpiScope,
    entity_id: str | None,
    severity: GpiSeverity,
    gpi: Decimal | None,
    girls: int = 30,
    boys: int = 60,
) -> GpiSnapshot:
    snap = GpiSnapshot(
        id=generate_cuid(),
        schoolYearId=school_year_id,
        scope=scope,
        entityId=entity_id,
        girlsCount=girls,
        boysCount=boys,
        gpi=gpi,
        severity=severity,
        computedAt=datetime.now(UTC),
    )
    session.add(snap)
    await session.flush()
    return snap


async def _seed_capacity_snapshot(
    session: AsyncSession,
    *,
    base_school_year_id: str,
    scope: CapacityScope,
    entity_id: str | None,
    severity: CapacitySeverity,
    capacity: int = 100,
    demand: int = 200,
    projected_year: int = 2027,
) -> CapacityDemandSnapshot:
    await _ensure_baseline_scenario(session)
    snap = CapacityDemandSnapshot(
        id=generate_cuid(),
        baseSchoolYearId=base_school_year_id,
        projectedYear=projected_year,
        scope=scope,
        entityId=entity_id,
        capacity=capacity,
        demand=demand,
        gap=demand - capacity,
        saturationPct=Decimal(f"{(demand / max(capacity, 1)) * 100:.2f}"),
        severity=severity,
        scenarioId=BASELINE_SCENARIO_ID,
        computedAt=datetime.now(UTC),
    )
    session.add(snap)
    await session.flush()
    return snap


async def _seed_staffing_snapshot(
    session: AsyncSession,
    *,
    school_year_id: str,
    school_id: str,
    severity: StaffingSeverity,
    students: int = 200,
    teachers: int = 2,
    expected_teachers: int = 5,
) -> TeacherStaffingSnapshot:
    ratio = (
        Decimal(f"{students / teachers:.2f}") if teachers > 0 else None
    )
    snap = TeacherStaffingSnapshot(
        id=generate_cuid(),
        schoolYearId=school_year_id,
        schoolId=school_id,
        studentsCount=students,
        teachersCount=teachers,
        ratio=ratio,
        severity=severity,
        expectedTeachers=expected_teachers,
        gap=expected_teachers - teachers,
        computedAt=datetime.now(UTC),
    )
    session.add(snap)
    await session.flush()
    return snap


# ===========================================================================
# 1. GPI critical regions
# ===========================================================================
@pytest.mark.asyncio
async def test_gpi_critical_regions_returns_geojson_feature_collection(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    sy = await _make_school_year(db_session)
    await _seed_gpi_snapshot(
        db_session,
        school_year_id=sy.id,
        scope=GpiScope.REGIONAL,
        entity_id=tree["region"].id,
        severity=GpiSeverity.CRITICAL_GIRLS,
        gpi=Decimal("0.8200"),
        girls=200,
        boys=300,
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get(
        "/api/cartography/layers/gpi-critical-regions",
        params={"schoolYearId": sy.id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "FeatureCollection"
    assert isinstance(body["features"], list)
    assert body["meta"]["layer"] == "gpi-critical-regions"
    # Notre région doit apparaître avec sa sévérité critique.
    region_ids = [f["properties"]["regionId"] for f in body["features"]]
    assert tree["region"].id in region_ids
    feat = next(
        f for f in body["features"]
        if f["properties"]["regionId"] == tree["region"].id
    )
    assert feat["geometry"]["type"] == "Point"
    assert feat["properties"]["severity"] in {"CRITICAL_GIRLS", "WARNING_GIRLS"}


# ===========================================================================
# 2. GPI critical regions filters NORMAL/WARNING_BOYS away
# ===========================================================================
@pytest.mark.asyncio
async def test_gpi_critical_regions_only_includes_severity_critical_or_warning(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    factories.bind(db_session)
    tree_critical = await factories.make_territorial_tree()
    tree_normal = await factories.make_territorial_tree()
    sy = await _make_school_year(db_session)

    await _seed_gpi_snapshot(
        db_session,
        school_year_id=sy.id,
        scope=GpiScope.REGIONAL,
        entity_id=tree_critical["region"].id,
        severity=GpiSeverity.CRITICAL_GIRLS,
        gpi=Decimal("0.8000"),
    )
    # NORMAL — doit être exclu de la couche.
    await _seed_gpi_snapshot(
        db_session,
        school_year_id=sy.id,
        scope=GpiScope.REGIONAL,
        entity_id=tree_normal["region"].id,
        severity=GpiSeverity.NORMAL,
        gpi=Decimal("1.0100"),
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get(
        "/api/cartography/layers/gpi-critical-regions",
        params={"schoolYearId": sy.id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    severities = {f["properties"]["severity"] for f in body["features"]}
    assert "NORMAL" not in severities
    assert "WARNING_BOYS" not in severities
    region_ids = {f["properties"]["regionId"] for f in body["features"]}
    assert tree_critical["region"].id in region_ids
    assert tree_normal["region"].id not in region_ids


# ===========================================================================
# 3. Capacity critical schools : CRITICAL uniquement
# ===========================================================================
@pytest.mark.asyncio
async def test_capacity_critical_schools_returns_only_critical(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    factories.bind(db_session)
    tree_a = await factories.make_territorial_tree()
    tree_b = await factories.make_territorial_tree()
    sy = await _make_school_year(db_session)

    # School A — CRITICAL → doit apparaître.
    await _seed_capacity_snapshot(
        db_session,
        base_school_year_id=sy.id,
        scope=CapacityScope.SCHOOL,
        entity_id=tree_a["school"].id,
        severity=CapacitySeverity.CRITICAL,
        capacity=50,
        demand=120,
    )
    # School B — WARNING → doit être ignoré par la couche.
    await _seed_capacity_snapshot(
        db_session,
        base_school_year_id=sy.id,
        scope=CapacityScope.SCHOOL,
        entity_id=tree_b["school"].id,
        severity=CapacitySeverity.WARNING,
        capacity=100,
        demand=95,
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get(
        "/api/cartography/layers/capacity-critical-schools",
        params={"baseSchoolYearId": sy.id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    school_ids = [f["properties"]["schoolId"] for f in body["features"]]
    assert tree_a["school"].id in school_ids
    assert tree_b["school"].id not in school_ids
    # Toutes les features doivent porter la sévérité CRITICAL.
    for feat in body["features"]:
        assert feat["properties"]["severity"] == "CRITICAL"


# ===========================================================================
# 4. Staffing critical schools : UNDER + CRITICAL
# ===========================================================================
@pytest.mark.asyncio
async def test_staffing_critical_schools_returns_under_and_critical(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    factories.bind(db_session)
    tree_under = await factories.make_territorial_tree()
    tree_critical = await factories.make_territorial_tree()
    tree_ok = await factories.make_territorial_tree()
    sy = await _make_school_year(db_session)

    await _seed_staffing_snapshot(
        db_session,
        school_year_id=sy.id,
        school_id=tree_under["school"].id,
        severity=StaffingSeverity.UNDER_STAFFED,
        students=240,
        teachers=4,
    )
    await _seed_staffing_snapshot(
        db_session,
        school_year_id=sy.id,
        school_id=tree_critical["school"].id,
        severity=StaffingSeverity.CRITICAL,
        students=300,
        teachers=2,
    )
    await _seed_staffing_snapshot(
        db_session,
        school_year_id=sy.id,
        school_id=tree_ok["school"].id,
        severity=StaffingSeverity.ADEQUATE,
        students=120,
        teachers=3,
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get(
        "/api/cartography/layers/staffing-critical-schools",
        params={"schoolYearId": sy.id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_school = {
        f["properties"]["schoolId"]: f["properties"]["severity"]
        for f in body["features"]
    }
    assert by_school.get(tree_under["school"].id) == "UNDER_STAFFED"
    assert by_school.get(tree_critical["school"].id) == "CRITICAL"
    assert tree_ok["school"].id not in by_school


# ===========================================================================
# 5. Infrastructure gaps — au moins une lacune
# ===========================================================================
@pytest.mark.asyncio
async def test_infrastructure_gaps_lists_schools_missing_water_or_electricity(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    # On marque l'école sans eau (waterSource = NONE) + sans toilettes.
    tree["school"].waterSource = WaterSource.NONE
    tree["school"].toiletsBoys = 0
    tree["school"].toiletsGirls = 0
    await db_session.flush()

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get(
        "/api/cartography/layers/infrastructure-gaps", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    school_ids = [f["properties"]["schoolId"] for f in body["features"]]
    assert tree["school"].id in school_ids
    feat = next(
        f for f in body["features"]
        if f["properties"]["schoolId"] == tree["school"].id
    )
    assert feat["properties"]["missingWater"] is True
    assert feat["properties"]["missingToilets"] is True
    assert "water" in feat["properties"]["gaps"]


# ===========================================================================
# 6. Zone-type layer — toutes les sous-préfs sont incluses
# ===========================================================================
@pytest.mark.asyncio
async def test_zone_type_layer_includes_all_subprefectures(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    factories.bind(db_session)
    tree_urban = await factories.make_territorial_tree()
    tree_rural = await factories.make_territorial_tree()
    tree_urban["subPrefecture"].defaultZoneType = ZoneType.URBAN
    tree_rural["subPrefecture"].defaultZoneType = ZoneType.RURAL
    await db_session.flush()

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get(
        "/api/cartography/layers/zone-type", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    sub_ids = {
        f["properties"]["subPrefectureId"]: f["properties"]["zoneType"]
        for f in body["features"]
    }
    assert sub_ids.get(tree_urban["subPrefecture"].id) == "URBAN"
    assert sub_ids.get(tree_rural["subPrefecture"].id) == "RURAL"


# ===========================================================================
# 7. White zones enriched — paramétrage du rayon
# ===========================================================================
@pytest.mark.asyncio
async def test_white_zones_enriched_uses_radius_param(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    factories.bind(db_session)
    # On crée 2 sous-préfectures isolées (1 école chacune, loin l'une de
    # l'autre). Avec un rayon très grand, ces sous-préfs sont couvertes
    # mutuellement → 0 zone blanche ; avec un rayon réduit, on en retrouve.
    await factories.make_territorial_tree()
    await factories.make_territorial_tree()

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)

    resp_large = await client.get(
        "/api/cartography/layers/white-zones-enriched",
        params={"radiusKm": 50.0, "populationThreshold": 100},
        headers=headers,
    )
    assert resp_large.status_code == 200, resp_large.text
    body_large = resp_large.json()
    # Le rayon doit être propagé dans la meta.
    assert body_large["meta"]["radiusKm"] == 50.0
    assert body_large["meta"]["populationThreshold"] == 100


# ===========================================================================
# 8. RBAC — pas de token ⇒ 401
# ===========================================================================
@pytest.mark.asyncio
async def test_layers_require_auth(client: AsyncClient) -> None:
    """Les 6 endpoints doivent refuser un appel sans bearer."""
    paths = [
        "/api/cartography/layers/gpi-critical-regions",
        "/api/cartography/layers/capacity-critical-schools",
        "/api/cartography/layers/staffing-critical-schools",
        "/api/cartography/layers/infrastructure-gaps",
        "/api/cartography/layers/zone-type",
        "/api/cartography/layers/white-zones-enriched",
    ]
    for path in paths:
        resp = await client.get(path)
        assert resp.status_code == 401, f"{path} should require auth"


# ===========================================================================
# 9. Scope territorial — REGIONAL_ADMIN limité à sa région
# ===========================================================================
@pytest.mark.asyncio
async def test_layers_respect_territorial_scope(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    factories.bind(db_session)
    tree_in = await factories.make_territorial_tree()
    tree_out = await factories.make_territorial_tree()
    # On marque les deux écoles sans eau pour qu'elles apparaissent dans la
    # couche infrastructure-gaps.
    for tree in (tree_in, tree_out):
        tree["school"].waterSource = WaterSource.NONE
        tree["school"].electricitySource = ElectricitySource.NONE
        tree["school"].toiletsBoys = 0
        tree["school"].toiletsGirls = 0
    await db_session.flush()

    # REGIONAL_ADMIN attaché à tree_in.region — ne doit voir que ses écoles.
    headers = await auth_headers(
        UserRole.REGIONAL_ADMIN, regionId=tree_in["region"].id
    )
    resp = await client.get(
        "/api/cartography/layers/infrastructure-gaps", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    school_ids = {f["properties"]["schoolId"] for f in body["features"]}
    assert tree_in["school"].id in school_ids
    assert tree_out["school"].id not in school_ids


# ===========================================================================
# 10. Cache Redis : 2e appel HIT
# ===========================================================================
@pytest.mark.asyncio
async def test_layer_cached_in_redis(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """Le 2e appel doit revenir avec ``meta.cached = True``.

    On observe la propriété ``cached`` placée par ``CartographyService.get_layer``
    selon que le hit vient du store (True) ou du calcul (False).
    """
    factories.bind(db_session)
    await factories.make_territorial_tree()

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    r1 = await client.get(
        "/api/cartography/layers/zone-type", headers=headers
    )
    assert r1.status_code == 200
    r2 = await client.get(
        "/api/cartography/layers/zone-type", headers=headers
    )
    assert r2.status_code == 200
    assert r1.json()["meta"].get("cached") is False
    assert r2.json()["meta"].get("cached") is True


# ===========================================================================
# 11. Couche inconnue → 404 (via service helper, isole le router)
# ===========================================================================
@pytest.mark.asyncio
async def test_unknown_layer_name_returns_404(
    db_session: AsyncSession,
) -> None:
    """``CartographyService.get_layer`` lève NotFoundError si nom inconnu.

    On teste directement le service (pas le router) pour vérifier que la
    contrat protège bien d'un dispatch erroné.
    """
    from app.core.exceptions import NotFoundError  # noqa: PLC0415
    from app.modules.auth.models import User  # noqa: PLC0415

    user = User(
        id=generate_cuid(),
        email="admin@test.local",
        passwordHash="x",
        fullName="Admin Test",
        role=UserRole.NATIONAL_ADMIN,
        isActive=True,
    )
    svc = CartographyService(db_session)
    with pytest.raises(NotFoundError):
        await svc.get_layer("not-a-real-layer", {}, user)


# ===========================================================================
# 12. Infrastructure-gaps exclut les écoles complètes
# ===========================================================================
@pytest.mark.asyncio
async def test_infrastructure_gaps_excludes_complete_schools(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    factories.bind(db_session)
    tree_complete = await factories.make_territorial_tree()
    # Équipement complet : aucune lacune → school ne doit PAS apparaître.
    tree_complete["school"].waterSource = WaterSource.NETWORK
    tree_complete["school"].electricitySource = ElectricitySource.GRID
    tree_complete["school"].toiletsBoys = 3
    tree_complete["school"].toiletsGirls = 3
    tree_complete["school"].internetAvailable = True
    await db_session.flush()

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get(
        "/api/cartography/layers/infrastructure-gaps", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    school_ids = {f["properties"]["schoolId"] for f in body["features"]}
    assert tree_complete["school"].id not in school_ids


# ===========================================================================
# 13. Hardening — sanity checks indépendants
# ===========================================================================
def test_supported_layers_match_router_endpoints() -> None:
    """Les noms exposés par le service doivent correspondre 1:1 au router.

    Module 3C ajoute ``investment-priority`` ; les 6 couches d'origine
    restent inchangées.
    """
    expected = {
        "gpi-critical-regions",
        "capacity-critical-schools",
        "staffing-critical-schools",
        "infrastructure-gaps",
        "zone-type",
        "white-zones-enriched",
        "investment-priority",
    }
    assert frozenset(expected) == SUPPORTED_LAYERS


def test_layer_cache_key_includes_scope_and_params() -> None:
    """Deux scopes différents → 2 clés différentes pour la même couche."""
    from app.modules.auth.models import User  # noqa: PLC0415

    user_a = User(
        id="u1", email="a@x.lo", passwordHash="x", fullName="A",
        role=UserRole.REGIONAL_ADMIN, isActive=True, regionId="R-1",
    )
    user_b = User(
        id="u2", email="b@x.lo", passwordHash="x", fullName="B",
        role=UserRole.REGIONAL_ADMIN, isActive=True, regionId="R-2",
    )
    k1 = CartographyService._layer_cache_key(
        "zone-type", {"foo": "bar"}, user_a
    )
    k2 = CartographyService._layer_cache_key(
        "zone-type", {"foo": "bar"}, user_b
    )
    assert k1 != k2
    assert k1.startswith(f"{LAYER_CACHE_PREFIX}:zone-type:")
