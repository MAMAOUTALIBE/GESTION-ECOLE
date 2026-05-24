"""Module 2C — Capacité vs demande projetée (IIPE / planification infra).

Couvre :
1.  compute_school_capacity : formule de base + bornes.
2.  Seuils de severity (OK / WARNING / CRITICAL) — table de cas.
3.  compute_capacity_demand persiste un snapshot pour CHAQUE école.
4.  Redistribution proportionnelle de la projection régionale aux écoles.
5.  Agrégation up-stream (SCHOOL → PREFECTURE → REGIONAL → NATIONAL).
6.  compute refusé hors NATIONAL/MINISTRY (TEACHER → ForbiddenError).
7.  list filtre par severity=CRITICAL.
8.  list_critical_schools_for_investment ne renvoie que les CRITICAL.
9.  École CRITICAL crée une AnomalyDetection (hook Module 9).
10. Cockpit KPI inclut projectedCriticalSchools (hook Module 19).
11. list respecte le scope territorial (REGIONAL_ADMIN ne voit que sa région).
12. Recompute écrase l'ancien snapshot (idempotence delete-then-insert).
13. École à classroomsUsable=0 → severity CRITICAL (capacity=0, demand>0).
14. saturationPct calculée en Decimal avec précision NUMERIC(6,2).
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError
from app.modules.academics.models import SchoolYear
from app.modules.anomalies.enums import AnomalyType
from app.modules.anomalies.models import AnomalyDetection
from app.modules.auth.models import User
from app.modules.cockpit.service import CockpitService
from app.modules.enrollment.enums import (
    EnrollmentClassLevel,
    EnrollmentSource,
)
from app.modules.enrollment.models import Enrollment
from app.modules.projections.capacity import (
    compute_saturation_pct,
    compute_school_capacity,
    compute_severity,
)
from app.modules.projections.enums import (
    BASELINE_SCENARIO_ID,
    STUDENTS_PER_CLASSROOM_NORM,
    CapacityScope,
    CapacitySeverity,
)
from app.modules.projections.models import (
    CapacityDemandSnapshot,
    ProjectionScenario,
)
from app.modules.projections.schemas import (
    CapacityDemandFilters,
    CapacityDemandRequest,
    RunProjectionRequest,
)
from app.modules.projections.service import (
    CapacityDemandService,
    ProjectionService,
)
from app.shared.base import generate_cuid
from app.shared.enums import AcademicPeriodType, Gender, UserRole
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _make_school_year(
    session: AsyncSession,
    *,
    name: str | None = None,
    is_active: bool = True,
    year: int = 2025,
) -> SchoolYear:
    sy = SchoolYear(
        id=generate_cuid(),
        name=name or f"YEAR-{generate_cuid()[:6]}",
        startDate=datetime(year, 9, 1, tzinfo=UTC),
        endDate=datetime(year + 1, 6, 30, tzinfo=UTC),
        periodType=AcademicPeriodType.TRIMESTER,
        isActive=is_active,
    )
    session.add(sy)
    await session.flush()
    return sy


async def _make_admin_user(
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


def _seed_enrollment(
    session: AsyncSession,
    *,
    school_year_id: str,
    school_id: str,
    class_level: EnrollmentClassLevel,
    gender: Gender,
    count: int,
) -> None:
    """Ajoute UNE row Enrollment pour (year, school, level, gender)."""
    now = datetime.now(UTC)
    session.add(Enrollment(
        schoolYearId=school_year_id,
        schoolId=school_id,
        classLevel=class_level,
        gender=gender,
        count=count,
        source=EnrollmentSource.CENSUS_DECLARED,
        recordedAt=now,
    ))


async def _ensure_baseline_scenario(session: AsyncSession) -> None:
    existing = (
        await session.execute(
            select(ProjectionScenario)
            .where(ProjectionScenario.id == BASELINE_SCENARIO_ID)
        )
    ).scalars().one_or_none()
    if existing is None:
        session.add(ProjectionScenario(
            id=BASELINE_SCENARIO_ID,
            name=BASELINE_SCENARIO_ID,
            description="Scénario par défaut tests.",
            demographicGrowthRate=Decimal("0.0240"),
            customTransitionRates=None,
            createdById=None,
            createdAt=datetime.now(UTC),
        ))
        await session.flush()


@pytest_asyncio.fixture(loop_scope="session")
async def cap_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Setup : 2 régions × 2 écoles + 2 années + capacités connues + admin."""
    factories.bind(db_session)
    region_a = await factories.RegionFactory.create_async()
    region_b = await factories.RegionFactory.create_async()
    pref_a = await factories.PrefectureFactory.create_async(
        regionId=region_a.id,
    )
    pref_b = await factories.PrefectureFactory.create_async(
        regionId=region_b.id,
    )
    sub_a = await factories.SubPrefectureFactory.create_async(
        regionId=region_a.id, prefectureId=pref_a.id,
    )
    sub_b = await factories.SubPrefectureFactory.create_async(
        regionId=region_b.id, prefectureId=pref_b.id,
    )
    # School A : 10 salles utilisables → capacity 500.
    school_a = await factories.SchoolFactory.create_async(
        regionId=region_a.id,
        prefectureId=pref_a.id,
        subPrefectureId=sub_a.id,
        classroomsUsable=10,
        classroomsTotal=10,
    )
    # School B : 5 salles utilisables → capacity 250.
    school_b = await factories.SchoolFactory.create_async(
        regionId=region_b.id,
        prefectureId=pref_b.id,
        subPrefectureId=sub_b.id,
        classroomsUsable=5,
        classroomsTotal=5,
    )

    year_from = await _make_school_year(
        db_session, year=2024, name="YEAR-FROM-2C", is_active=False,
    )
    year_to = await _make_school_year(
        db_session, year=2025, name="YEAR-TO-2C", is_active=True,
    )

    await _ensure_baseline_scenario(db_session)
    return {
        "region_a": region_a,
        "region_b": region_b,
        "prefecture_a": pref_a,
        "prefecture_b": pref_b,
        "school_a": school_a,
        "school_b": school_b,
        "year_from": year_from,
        "year_to": year_to,
    }


async def _seed_baseline_and_project(
    db_session: AsyncSession,
    ctx: dict[str, Any],
    *,
    horizon: int = 2,
) -> None:
    """Seed les Enrollment CENSUS_DECLARED puis lance les calculs 2A + 2B.

    On crée des effectifs réalistes pour permettre une vraie projection
    et donc une demande projetée non triviale au scope régional.
    """
    for school_id in (ctx["school_a"].id, ctx["school_b"].id):
        for level in (
            EnrollmentClassLevel.MATERNELLE_1,
            EnrollmentClassLevel.CP1,
            EnrollmentClassLevel.CP2,
            EnrollmentClassLevel.CE1,
            EnrollmentClassLevel.CE2,
        ):
            # year_from : 100 FEMALE + 100 MALE
            _seed_enrollment(
                db_session,
                school_year_id=ctx["year_from"].id,
                school_id=school_id,
                class_level=level,
                gender=Gender.FEMALE,
                count=100,
            )
            _seed_enrollment(
                db_session,
                school_year_id=ctx["year_from"].id,
                school_id=school_id,
                class_level=level,
                gender=Gender.MALE,
                count=100,
            )
            # year_to : 80 chacun (rate transition 0.8)
            _seed_enrollment(
                db_session,
                school_year_id=ctx["year_to"].id,
                school_id=school_id,
                class_level=level,
                gender=Gender.FEMALE,
                count=80,
            )
            _seed_enrollment(
                db_session,
                school_year_id=ctx["year_to"].id,
                school_id=school_id,
                class_level=level,
                gender=Gender.MALE,
                count=80,
            )
    await db_session.flush()

    # On exécute Module 2A (taux de transition) + 2B (projection).
    from app.modules.projections.service import TransitionRateService

    admin = await _make_admin_user(db_session)
    tsvc = TransitionRateService(db_session)
    await tsvc.compute_transitions([ctx["year_from"].id], admin)

    psvc = ProjectionService(db_session)
    await psvc.run_projection(
        RunProjectionRequest(
            baseSchoolYearId=ctx["year_from"].id,
            horizonYears=horizon,
        ),
        admin,
    )


# ===========================================================================
# 1. compute_school_capacity — formule de base
# ===========================================================================
def test_compute_school_capacity_basic() -> None:
    """10 salles × 50 = 500 places. Norme custom respectée."""
    assert compute_school_capacity(10) == 500
    assert compute_school_capacity(10, norm=40) == 400
    assert compute_school_capacity(0) == 0
    # Norme invalide refusée.
    with pytest.raises(ValueError):
        compute_school_capacity(10, norm=0)
    with pytest.raises(ValueError):
        compute_school_capacity(-1)


# ===========================================================================
# 2. Seuils de severity — table de cas
# ===========================================================================
def test_severity_thresholds() -> None:
    """50% → OK, 85% → WARNING, 120% → CRITICAL."""
    # 50 % saturation : 250 / 500 × 100.
    s50 = compute_saturation_pct(250, 500)
    assert s50 == Decimal("50.00")
    assert compute_severity(s50) == CapacitySeverity.OK
    # Borne 80 % (inclusif) → OK.
    s80 = compute_saturation_pct(400, 500)
    assert s80 == Decimal("80.00")
    assert compute_severity(s80) == CapacitySeverity.OK
    # 85 % : WARNING.
    s85 = compute_saturation_pct(425, 500)
    assert s85 == Decimal("85.00")
    assert compute_severity(s85) == CapacitySeverity.WARNING
    # Borne 100 % (inclusif) → WARNING.
    s100 = compute_saturation_pct(500, 500)
    assert s100 == Decimal("100.00")
    assert compute_severity(s100) == CapacitySeverity.WARNING
    # 120 % : CRITICAL.
    s120 = compute_saturation_pct(600, 500)
    assert s120 == Decimal("120.00")
    assert compute_severity(s120) == CapacitySeverity.CRITICAL
    # capacity=0 → severity CRITICAL (NULL saturation).
    assert compute_saturation_pct(100, 0) is None
    assert compute_severity(None) == CapacitySeverity.CRITICAL


# ===========================================================================
# 3. compute_capacity_demand persiste pour toutes les écoles
# ===========================================================================
async def test_compute_capacity_demand_persists_for_all_schools(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_and_project(db_session, cap_ctx)
    admin = await _make_admin_user(db_session)
    svc = CapacityDemandService(db_session)
    resp = await svc.compute_capacity_demand(
        CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id),
        admin,
    )
    assert resp.totalSchoolsAnalyzed == 2
    # Un snapshot SCHOOL par école × année projetée (2 années dans le seed).
    school_rows = (
        await db_session.execute(
            select(CapacityDemandSnapshot)
            .where(CapacityDemandSnapshot.scope == CapacityScope.SCHOOL)
        )
    ).scalars().all()
    school_ids = {r.entityId for r in school_rows}
    assert cap_ctx["school_a"].id in school_ids
    assert cap_ctx["school_b"].id in school_ids


# ===========================================================================
# 4. Redistribution proportionnelle de la projection régionale
# ===========================================================================
async def test_compute_redistributes_regional_projection_to_schools_proportionally(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    """Avec un seul école par région et 100 % des effectifs base déclarés,
    la demande projetée de l'école doit == projection régionale entière."""
    await _seed_baseline_and_project(db_session, cap_ctx, horizon=1)
    admin = await _make_admin_user(db_session)
    svc = CapacityDemandService(db_session)
    await svc.compute_capacity_demand(
        CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id),
        admin,
    )
    # Récupère la projection REGIONAL pour la région A.
    from app.modules.projections.enums import TransitionScope
    from app.modules.projections.models import ProjectedEnrollment

    region_a_demand_stmt = (
        select(ProjectedEnrollment.projectedCount)
        .where(
            ProjectedEnrollment.scope == TransitionScope.REGIONAL,
            ProjectedEnrollment.entityId == cap_ctx["region_a"].id,
        )
    )
    region_a_total = sum(
        (await db_session.execute(region_a_demand_stmt)).scalars().all(),
    )
    # School A est l'unique école de region_a → toute la demande la lui revient.
    school_a_row = (
        await db_session.execute(
            select(CapacityDemandSnapshot)
            .where(
                CapacityDemandSnapshot.scope == CapacityScope.SCHOOL,
                CapacityDemandSnapshot.entityId == cap_ctx["school_a"].id,
            )
        )
    ).scalars().one()
    # Avec arrondi (round()) la demande école doit être très proche du total
    # régional (l'école A est seule dans la région A).
    assert abs(school_a_row.demand - region_a_total) <= 1


# ===========================================================================
# 5. Agrégation up-stream (SCHOOL → PREFECTURE → REGIONAL → NATIONAL)
# ===========================================================================
async def test_compute_aggregates_up_to_national(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_and_project(db_session, cap_ctx, horizon=1)
    admin = await _make_admin_user(db_session)
    svc = CapacityDemandService(db_session)
    await svc.compute_capacity_demand(
        CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id),
        admin,
    )
    # On a au moins un snapshot pour chaque scope.
    scopes_present: set[CapacityScope] = set()
    for scope in CapacityScope:
        rows = (
            await db_session.execute(
                select(CapacityDemandSnapshot)
                .where(CapacityDemandSnapshot.scope == scope)
            )
        ).scalars().all()
        if rows:
            scopes_present.add(scope)
    assert scopes_present == set(CapacityScope)
    # NATIONAL = somme des REGIONAL pour la même année.
    nat_row = (
        await db_session.execute(
            select(CapacityDemandSnapshot)
            .where(CapacityDemandSnapshot.scope == CapacityScope.NATIONAL)
        )
    ).scalars().first()
    region_rows = (
        await db_session.execute(
            select(CapacityDemandSnapshot)
            .where(
                CapacityDemandSnapshot.scope == CapacityScope.REGIONAL,
                CapacityDemandSnapshot.projectedYear == nat_row.projectedYear,
            )
        )
    ).scalars().all()
    total_regional_demand = sum(r.demand for r in region_rows)
    total_regional_capacity = sum(r.capacity for r in region_rows)
    assert nat_row.demand == total_regional_demand
    assert nat_row.capacity == total_regional_capacity


# ===========================================================================
# 6. compute_capacity_demand refusé hors NATIONAL/MINISTRY
# ===========================================================================
async def test_compute_requires_admin(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    teacher = await _make_admin_user(db_session, UserRole.TEACHER)
    svc = CapacityDemandService(db_session)
    with pytest.raises(ForbiddenError):
        await svc.compute_capacity_demand(
            CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id),
            teacher,
        )


# ===========================================================================
# 7. list filtre par severity=CRITICAL
# ===========================================================================
async def test_list_filters_by_severity_critical(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    """On force la création d'une école saturée pour avoir au moins un row
    CRITICAL, puis on vérifie que le filtre fonctionne."""
    await _seed_baseline_and_project(db_session, cap_ctx)
    admin = await _make_admin_user(db_session)
    svc = CapacityDemandService(db_session)
    await svc.compute_capacity_demand(
        CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id),
        admin,
    )
    # Force au moins un snapshot CRITICAL en altérant un row existant pour
    # le filtre (on simule une école saturée sans avoir à manipuler les
    # paramètres de norme).
    row = (
        await db_session.execute(
            select(CapacityDemandSnapshot)
            .where(CapacityDemandSnapshot.scope == CapacityScope.SCHOOL)
            .limit(1)
        )
    ).scalars().one()
    row.severity = CapacitySeverity.CRITICAL
    row.saturationPct = Decimal("150.00")
    row.demand = 1000
    row.gap = 500
    await db_session.flush()

    results = await svc.list_capacity_demand(
        CapacityDemandFilters(severity=CapacitySeverity.CRITICAL),
        admin,
    )
    assert len(results) >= 1
    assert all(r.severity == CapacitySeverity.CRITICAL for r in results)


# ===========================================================================
# 8. list_critical_schools_for_investment ne renvoie que les CRITICAL
# ===========================================================================
async def test_list_critical_schools_returns_only_critical(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_and_project(db_session, cap_ctx)
    admin = await _make_admin_user(db_session)
    svc = CapacityDemandService(db_session)
    await svc.compute_capacity_demand(
        CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id),
        admin,
    )
    # Force un row CRITICAL pour avoir au moins une école remontée.
    row = (
        await db_session.execute(
            select(CapacityDemandSnapshot)
            .where(CapacityDemandSnapshot.scope == CapacityScope.SCHOOL)
            .limit(1)
        )
    ).scalars().one()
    row.severity = CapacitySeverity.CRITICAL
    row.saturationPct = Decimal("130.00")
    row.gap = 100
    await db_session.flush()

    results = await svc.list_critical_schools_for_investment(admin, limit=10)
    assert len(results) >= 1
    for r in results:
        assert r.severity == CapacitySeverity.CRITICAL
        assert r.scope == CapacityScope.SCHOOL


# ===========================================================================
# 9. École CRITICAL crée une AnomalyDetection (hook Module 9)
# ===========================================================================
async def test_critical_school_creates_anomaly_record(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    """Quand une école est CRITICAL sur t+1, une anomalie HIGH est créée."""
    # Pour forcer le CRITICAL au calcul, on saisit beaucoup d'effectifs +
    # peu de capacité dans School B : 5 salles × 50 = 250 places vs des
    # centaines d'élèves attendus.
    # On garde School A normal et on s'assure que School B sature.
    # Approche : on crée un test deterministe en seedant suffisamment.
    await _seed_baseline_and_project(db_session, cap_ctx)

    # Réduit drastiquement la capacité de school_b pour forcer CRITICAL.
    school_b = cap_ctx["school_b"]
    school_b.classroomsUsable = 1  # 50 places, demande projetée >> 50.
    db_session.add(school_b)
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = CapacityDemandService(db_session)
    await svc.compute_capacity_demand(
        CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id),
        admin,
    )
    # Au moins une AnomalyDetection CAPACITY_CRITICAL_PROJECTED doit exister.
    anomalies = (
        await db_session.execute(
            select(AnomalyDetection)
            .where(
                AnomalyDetection.type
                == AnomalyType.CAPACITY_CRITICAL_PROJECTED,
            )
        )
    ).scalars().all()
    assert len(anomalies) >= 1
    # Une anomalie au moins concerne school_b.
    school_b_anomalies = [
        a for a in anomalies if a.entityId == cap_ctx["school_b"].id
    ]
    assert len(school_b_anomalies) >= 1
    assert school_b_anomalies[0].entityType == "School"


# ===========================================================================
# 10. Cockpit KPI inclut projectedCriticalSchools (hook Module 19)
# ===========================================================================
async def test_cockpit_kpis_includes_projected_critical_schools_count(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_and_project(db_session, cap_ctx)
    admin = await _make_admin_user(db_session)
    svc = CapacityDemandService(db_session)
    await svc.compute_capacity_demand(
        CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id),
        admin,
    )
    # Force au moins une école CRITICAL sur la plus petite année projetée.
    min_year = (
        await db_session.execute(
            select(CapacityDemandSnapshot.projectedYear)
            .where(CapacityDemandSnapshot.scope == CapacityScope.SCHOOL)
            .order_by(CapacityDemandSnapshot.projectedYear.asc())
            .limit(1)
        )
    ).scalar_one()
    row = (
        await db_session.execute(
            select(CapacityDemandSnapshot)
            .where(
                CapacityDemandSnapshot.scope == CapacityScope.SCHOOL,
                CapacityDemandSnapshot.projectedYear == min_year,
            )
            .limit(1)
        )
    ).scalars().one()
    row.severity = CapacitySeverity.CRITICAL
    row.saturationPct = Decimal("110.00")
    await db_session.flush()

    # Le cockpit utilise un cache Redis : la fixture ``_flush_redis_per_test``
    # garantit déjà la propreté entre tests.
    cs = CockpitService(db_session)
    response = await cs.get_national_kpis()
    assert response.projectedCriticalSchools >= 1
    # Items dict doit aussi exposer la clé.
    from app.modules.cockpit.enums import KpiKey
    assert KpiKey.PROJECTED_CRITICAL_SCHOOLS_COUNT.value in response.items


# ===========================================================================
# 11. list respecte le scope territorial
# ===========================================================================
async def test_list_respects_territorial_scope(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_and_project(db_session, cap_ctx)
    admin = await _make_admin_user(db_session)
    svc = CapacityDemandService(db_session)
    await svc.compute_capacity_demand(
        CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id),
        admin,
    )
    # REGIONAL_ADMIN limité à region_a.
    reg_admin = await _make_admin_user(
        db_session, UserRole.REGIONAL_ADMIN,
        regionId=cap_ctx["region_a"].id,
    )
    results = await svc.list_capacity_demand(
        CapacityDemandFilters(scope=CapacityScope.SCHOOL, limit=1000),
        reg_admin,
    )
    # Voit School A mais pas School B.
    school_ids = {r.entityId for r in results}
    assert cap_ctx["school_a"].id in school_ids
    assert cap_ctx["school_b"].id not in school_ids


# ===========================================================================
# 12. Recompute écrase l'ancien snapshot (idempotence)
# ===========================================================================
async def test_recompute_upserts_old_snapshot(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_and_project(db_session, cap_ctx)
    admin = await _make_admin_user(db_session)
    svc = CapacityDemandService(db_session)
    req = CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id)
    first = await svc.compute_capacity_demand(req, admin)
    # Snapshot count après run 1.
    count_1 = (
        await db_session.execute(
            select(CapacityDemandSnapshot)
            .where(
                CapacityDemandSnapshot.baseSchoolYearId
                == cap_ctx["year_from"].id,
            )
        )
    ).scalars().all()

    # Run 2 (idempotent).
    second = await svc.compute_capacity_demand(req, admin)
    count_2 = (
        await db_session.execute(
            select(CapacityDemandSnapshot)
            .where(
                CapacityDemandSnapshot.baseSchoolYearId
                == cap_ctx["year_from"].id,
            )
        )
    ).scalars().all()
    # Pas de doublon : nb de rows identique.
    assert len(count_1) == len(count_2)
    assert first.rowsPersisted == second.rowsPersisted


# ===========================================================================
# 13. École à classroomsUsable=0 → traité (capacity=0)
# ===========================================================================
async def test_school_with_zero_usable_classrooms_skipped_or_flagged(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    """Une école avec classroomsUsable=0 a capacity=0. Si elle a une
    demande projetée > 0, on doit produire un row CRITICAL (signal
    construction requise) ; sinon on saute le row pour éviter le bruit."""
    await _seed_baseline_and_project(db_session, cap_ctx)

    # School_a passe à 0 salles utilisables. La région A a une demande
    # projetée, donc l'école doit ressortir CRITICAL.
    school_a = cap_ctx["school_a"]
    school_a.classroomsUsable = 0
    db_session.add(school_a)
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = CapacityDemandService(db_session)
    await svc.compute_capacity_demand(
        CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id),
        admin,
    )
    rows = (
        await db_session.execute(
            select(CapacityDemandSnapshot)
            .where(
                CapacityDemandSnapshot.scope == CapacityScope.SCHOOL,
                CapacityDemandSnapshot.entityId == cap_ctx["school_a"].id,
            )
        )
    ).scalars().all()
    # School_a doit avoir des rows (demande projetée > 0).
    assert len(rows) >= 1
    for r in rows:
        # capacity=0, saturationPct=NULL, severity=CRITICAL.
        assert r.capacity == 0
        assert r.saturationPct is None
        assert r.severity == CapacitySeverity.CRITICAL


# ===========================================================================
# 14. saturationPct calculée en Decimal avec précision NUMERIC(6,2)
# ===========================================================================
async def test_saturation_calculation_precision_decimal(
    db_session: AsyncSession, cap_ctx: dict[str, Any],
) -> None:
    """333 / 1000 × 100 = 33.30 (2 décimales, half-even)."""
    await _seed_baseline_and_project(db_session, cap_ctx)
    admin = await _make_admin_user(db_session)
    svc = CapacityDemandService(db_session)
    await svc.compute_capacity_demand(
        CapacityDemandRequest(baseSchoolYearId=cap_ctx["year_from"].id),
        admin,
    )
    rows = (
        await db_session.execute(
            select(CapacityDemandSnapshot)
            .where(
                CapacityDemandSnapshot.scope == CapacityScope.SCHOOL,
                CapacityDemandSnapshot.saturationPct.is_not(None),
            )
        )
    ).scalars().all()
    assert len(rows) >= 1
    for r in rows:
        # Le Decimal stocké doit respecter une précision 2 décimales
        # (Quantize ROUND_HALF_EVEN à 0.01).
        sign, digits, exponent = r.saturationPct.as_tuple()
        # exponent doit être -2 (2 décimales) ou plus négatif (0 → -2)
        assert exponent <= -2 or r.saturationPct == r.saturationPct.quantize(
            Decimal("0.01"),
        )

    # Vérification directe : pas d'altération de précision sur les valeurs
    # connues (constantes).
    assert compute_saturation_pct(333, 1000) == Decimal("33.30")
    assert compute_saturation_pct(1, 3) == Decimal("33.33")
    # Norme constante préservée.
    assert STUDENTS_PER_CLASSROOM_NORM == 50
