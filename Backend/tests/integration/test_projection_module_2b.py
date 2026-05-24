"""Module 2B — Projection effectifs horizon 5 ans (IIPE-UNESCO).

Couvre :
1.  project_one_year applique un transition rate basique.
2.  project_one_year MATERNELLE_1 utilise demographic_growth.
3.  project_one_year fallback rate national si régional manquant.
4.  project_one_year garde le count précédent si aucun rate connu.
5.  run_projection horizon 5 crée 5 années de records.
6.  run_projection refusé hors NATIONAL/MINISTRY (TEACHER → ForbiddenError).
7.  RunProjectionRequest rejette horizon > 10 (422 via Pydantic).
8.  run_projection idempotent (delete-then-insert).
9.  Projections désagrégées par genre.
10. Projections désagrégées par région.
11. NATIONAL = somme des projections régionales.
12. get_projections filtre par année.
13. get_projections respecte le scope territorial.
14. create_scenario avec demographic_growth personnalisé.
15. list_scenarios renvoie tous les scénarios visibles.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from pydantic import ValidationError
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
from app.modules.projections.enums import (
    BASELINE_SCENARIO_ID,
    TransitionScope,
)
from app.modules.projections.models import (
    ProjectedEnrollment,
    ProjectionScenario,
)
from app.modules.projections.projection import project_one_year
from app.modules.projections.schemas import (
    ProjectionFilters,
    ProjectionScenarioCreate,
    RunProjectionRequest,
)
from app.modules.projections.service import (
    ProjectionService,
    TransitionRateService,
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
    """Garantit la présence du scénario BASELINE (seedé par la migration,
    mais les tests transactionnels peuvent partir d'une base recréée
    par create_all sans le seed alembic)."""
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
async def proj_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Setup : 2 régions × 2 écoles + 2 années + admin + scénario."""
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
    school_a = await factories.SchoolFactory.create_async(
        regionId=region_a.id,
        prefectureId=pref_a.id,
        subPrefectureId=sub_a.id,
    )
    school_b = await factories.SchoolFactory.create_async(
        regionId=region_b.id,
        prefectureId=pref_b.id,
        subPrefectureId=sub_b.id,
    )

    year_from = await _make_school_year(
        db_session, year=2024, name="YEAR-FROM-2B", is_active=False,
    )
    year_to = await _make_school_year(
        db_session, year=2025, name="YEAR-TO-2B", is_active=True,
    )

    await _ensure_baseline_scenario(db_session)
    return {
        "region_a": region_a,
        "region_b": region_b,
        "school_a": school_a,
        "school_b": school_b,
        "year_from": year_from,
        "year_to": year_to,
    }


# ===========================================================================
# 1. project_one_year applique un transition rate basique
# ===========================================================================
def test_project_one_year_basic_apply_transition_rate() -> None:
    """100 CP1 × rate 0.80 = 80 CP2 — arrondi entier."""
    prev = {
        ("R1", EnrollmentClassLevel.CP1, Gender.FEMALE): 100,
    }
    rates = {
        (
            TransitionScope.REGIONAL, "R1",
            EnrollmentClassLevel.CP1, Gender.FEMALE,
        ): Decimal("0.8000"),
    }
    out = project_one_year(prev, rates, demographic_growth=Decimal("0.0"))
    # CP2 FEMALE = 100 × 0.8 = 80.
    assert out[("R1", EnrollmentClassLevel.CP2, Gender.FEMALE)] == 80


# ===========================================================================
# 2. project_one_year MATERNELLE_1 utilise demographic_growth
# ===========================================================================
def test_project_one_year_maternelle_uses_demographic_growth() -> None:
    """MATERNELLE_1 = prev × (1 + growth)."""
    prev = {
        ("R1", EnrollmentClassLevel.MATERNELLE_1, Gender.FEMALE): 100,
        ("R1", EnrollmentClassLevel.MATERNELLE_1, Gender.MALE): 200,
    }
    out = project_one_year(
        prev, transition_rates={}, demographic_growth=Decimal("0.10"),
    )
    # 100 × 1.10 = 110, 200 × 1.10 = 220 (entier).
    assert out[("R1", EnrollmentClassLevel.MATERNELLE_1, Gender.FEMALE)] == 110
    assert out[("R1", EnrollmentClassLevel.MATERNELLE_1, Gender.MALE)] == 220


# ===========================================================================
# 3. project_one_year fallback rate national
# ===========================================================================
def test_project_one_year_missing_rate_fallbacks_to_national() -> None:
    """Pas de rate REGIONAL → on prend le rate NATIONAL."""
    prev = {
        ("R1", EnrollmentClassLevel.CP1, Gender.MALE): 200,
    }
    rates = {
        (
            TransitionScope.NATIONAL, None,
            EnrollmentClassLevel.CP1, Gender.MALE,
        ): Decimal("0.5000"),
    }
    out = project_one_year(prev, rates, demographic_growth=Decimal("0.0"))
    # CP2 MALE = 200 × 0.5 = 100 (fallback national).
    assert out[("R1", EnrollmentClassLevel.CP2, Gender.MALE)] == 100


# ===========================================================================
# 4. project_one_year garde le count précédent si aucun rate connu
# ===========================================================================
def test_project_one_year_missing_all_rates_keeps_previous_count() -> None:
    """Aucun rate régional ni national → garde le count précédent au
    MÊME niveau (signal data quality, pas de zéro silencieux)."""
    prev = {
        ("R1", EnrollmentClassLevel.CP1, Gender.FEMALE): 50,
        ("R1", EnrollmentClassLevel.CP2, Gender.FEMALE): 30,
    }
    out = project_one_year(prev, transition_rates={})
    # CP2 conservé tel quel.
    assert out[("R1", EnrollmentClassLevel.CP2, Gender.FEMALE)] == 30


# ===========================================================================
# Helper : alimente prev_enrollments + rates pour les tests DB
# ===========================================================================
async def _seed_baseline_data(
    db_session: AsyncSession, ctx: dict[str, Any],
) -> None:
    """Seed un Enrollment minimal + des transition rates Module 2A complets
    pour permettre une projection multi-années sans rate manquant.
    """
    for school_id in (ctx["school_a"].id, ctx["school_b"].id):
        # Effectifs initiaux par niveau (FEMALE seul pour simplifier).
        for level in (
            EnrollmentClassLevel.MATERNELLE_1,
            EnrollmentClassLevel.MATERNELLE_2,
            EnrollmentClassLevel.MATERNELLE_3,
            EnrollmentClassLevel.CP1,
            EnrollmentClassLevel.CP2,
            EnrollmentClassLevel.CE1,
            EnrollmentClassLevel.CE2,
            EnrollmentClassLevel.CM1,
            EnrollmentClassLevel.CM2,
        ):
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
                school_year_id=ctx["year_to"].id,
                school_id=school_id,
                class_level=level,
                gender=Gender.FEMALE,
                count=80,
            )
            _seed_enrollment(
                db_session,
                school_year_id=ctx["year_from"].id,
                school_id=school_id,
                class_level=level,
                gender=Gender.MALE,
                count=100,
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

    # On déclenche le calcul des transition rates Module 2A pour
    # alimenter la projection.
    admin = await _make_admin_user(db_session)
    tsvc = TransitionRateService(db_session)
    await tsvc.compute_transitions([ctx["year_from"].id], admin)


# ===========================================================================
# 5. run_projection horizon 5 crée 5 années de records
# ===========================================================================
async def test_run_projection_horizon_5_creates_5_years_of_records(
    db_session: AsyncSession, proj_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_data(db_session, proj_ctx)
    admin = await _make_admin_user(db_session)
    svc = ProjectionService(db_session)
    result = await svc.run_projection(
        RunProjectionRequest(
            baseSchoolYearId=proj_ctx["year_from"].id,
            horizonYears=5,
        ),
        admin,
    )
    assert result.horizonYears == 5
    # On doit avoir des projections sur 5 années calendaires distinctes.
    years = (
        await db_session.execute(
            select(ProjectedEnrollment.projectedYear)
            .where(
                ProjectedEnrollment.baseSchoolYearId
                == proj_ctx["year_from"].id,
            )
            .distinct()
        )
    ).scalars().all()
    assert len(set(years)) == 5
    # Les 5 années sont consécutives, démarrant à year_from.endDate.year + 1.
    base_year = proj_ctx["year_from"].endDate.year
    assert set(years) == {base_year + k for k in (1, 2, 3, 4, 5)}


# ===========================================================================
# 6. run_projection refusé hors NATIONAL/MINISTRY
# ===========================================================================
async def test_run_projection_requires_admin(
    db_session: AsyncSession, proj_ctx: dict[str, Any],
) -> None:
    teacher = await _make_admin_user(db_session, UserRole.TEACHER)
    svc = ProjectionService(db_session)
    with pytest.raises(ForbiddenError):
        await svc.run_projection(
            RunProjectionRequest(
                baseSchoolYearId=proj_ctx["year_from"].id,
                horizonYears=3,
            ),
            teacher,
        )


# ===========================================================================
# 7. RunProjectionRequest rejette horizon > 10
# ===========================================================================
def test_run_projection_rejects_horizon_above_10() -> None:
    """Pydantic doit refuser horizonYears > 10 (signal de saisie aberrante)."""
    with pytest.raises(ValidationError):
        RunProjectionRequest(
            baseSchoolYearId="any-id", horizonYears=11,
        )
    # Cas limite inclusif : 10 est OK.
    ok = RunProjectionRequest(
        baseSchoolYearId="any-id", horizonYears=10,
    )
    assert ok.horizonYears == 10
    # Cas <= 0 refusé aussi.
    with pytest.raises(ValidationError):
        RunProjectionRequest(
            baseSchoolYearId="any-id", horizonYears=0,
        )


# ===========================================================================
# 8. run_projection idempotent (delete-then-insert)
# ===========================================================================
async def test_run_projection_idempotent_upsert(
    db_session: AsyncSession, proj_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_data(db_session, proj_ctx)
    admin = await _make_admin_user(db_session)
    svc = ProjectionService(db_session)
    req = RunProjectionRequest(
        baseSchoolYearId=proj_ctx["year_from"].id, horizonYears=3,
    )
    first = await svc.run_projection(req, admin)
    second = await svc.run_projection(req, admin)
    # Stable : même nombre de rows après ré-exécution.
    rows = (
        await db_session.execute(
            select(ProjectedEnrollment).where(
                ProjectedEnrollment.baseSchoolYearId
                == proj_ctx["year_from"].id,
                ProjectedEnrollment.scenarioId == BASELINE_SCENARIO_ID,
            )
        )
    ).scalars().all()
    assert len(rows) == second.projectedRows
    assert first.projectedRows == second.projectedRows


# ===========================================================================
# 9. Projections désagrégées par genre
# ===========================================================================
async def test_projections_disaggregated_by_gender(
    db_session: AsyncSession, proj_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_data(db_session, proj_ctx)
    admin = await _make_admin_user(db_session)
    svc = ProjectionService(db_session)
    await svc.run_projection(
        RunProjectionRequest(
            baseSchoolYearId=proj_ctx["year_from"].id, horizonYears=2,
        ),
        admin,
    )
    rows = (
        await db_session.execute(
            select(ProjectedEnrollment).where(
                ProjectedEnrollment.scope == TransitionScope.REGIONAL,
                ProjectedEnrollment.entityId == proj_ctx["region_a"].id,
            )
        )
    ).scalars().all()
    genders = {r.gender for r in rows}
    # On a au moins FEMALE et MALE séparés.
    assert Gender.FEMALE in genders
    assert Gender.MALE in genders


# ===========================================================================
# 10. Projections désagrégées par région
# ===========================================================================
async def test_projections_disaggregated_by_region(
    db_session: AsyncSession, proj_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_data(db_session, proj_ctx)
    admin = await _make_admin_user(db_session)
    svc = ProjectionService(db_session)
    await svc.run_projection(
        RunProjectionRequest(
            baseSchoolYearId=proj_ctx["year_from"].id, horizonYears=2,
        ),
        admin,
    )
    regional_rows = (
        await db_session.execute(
            select(ProjectedEnrollment).where(
                ProjectedEnrollment.scope == TransitionScope.REGIONAL,
            )
        )
    ).scalars().all()
    region_ids = {r.entityId for r in regional_rows}
    assert proj_ctx["region_a"].id in region_ids
    assert proj_ctx["region_b"].id in region_ids


# ===========================================================================
# 11. NATIONAL = somme des projections régionales
# ===========================================================================
async def test_national_projection_is_sum_of_regional(
    db_session: AsyncSession, proj_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_data(db_session, proj_ctx)
    admin = await _make_admin_user(db_session)
    svc = ProjectionService(db_session)
    await svc.run_projection(
        RunProjectionRequest(
            baseSchoolYearId=proj_ctx["year_from"].id, horizonYears=1,
        ),
        admin,
    )
    base_year = proj_ctx["year_from"].endDate.year
    target_year = base_year + 1

    # Pour CP1 FEMALE année t+1 : somme régions doit == NATIONAL.
    regional_rows = (
        await db_session.execute(
            select(ProjectedEnrollment).where(
                ProjectedEnrollment.projectedYear == target_year,
                ProjectedEnrollment.scope == TransitionScope.REGIONAL,
                ProjectedEnrollment.classLevel == EnrollmentClassLevel.CP1,
                ProjectedEnrollment.gender == Gender.FEMALE,
            )
        )
    ).scalars().all()
    total_regional = sum(r.projectedCount for r in regional_rows)

    national_row = (
        await db_session.execute(
            select(ProjectedEnrollment).where(
                ProjectedEnrollment.projectedYear == target_year,
                ProjectedEnrollment.scope == TransitionScope.NATIONAL,
                ProjectedEnrollment.classLevel == EnrollmentClassLevel.CP1,
                ProjectedEnrollment.gender == Gender.FEMALE,
            )
        )
    ).scalars().one()
    assert national_row.projectedCount == total_regional


# ===========================================================================
# 12. get_projections filtre par année
# ===========================================================================
async def test_get_projections_filters_by_year(
    db_session: AsyncSession, proj_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_data(db_session, proj_ctx)
    admin = await _make_admin_user(db_session)
    svc = ProjectionService(db_session)
    await svc.run_projection(
        RunProjectionRequest(
            baseSchoolYearId=proj_ctx["year_from"].id, horizonYears=3,
        ),
        admin,
    )
    target_year = proj_ctx["year_from"].endDate.year + 2

    results = await svc.get_projections(
        ProjectionFilters(projectedYear=target_year),
        admin,
    )
    assert len(results) > 0
    for r in results:
        assert r.projectedYear == target_year


# ===========================================================================
# 13. get_projections respecte le scope territorial
# ===========================================================================
async def test_get_projections_respects_territorial_scope(
    db_session: AsyncSession, proj_ctx: dict[str, Any],
) -> None:
    await _seed_baseline_data(db_session, proj_ctx)
    admin = await _make_admin_user(db_session)
    svc = ProjectionService(db_session)
    await svc.run_projection(
        RunProjectionRequest(
            baseSchoolYearId=proj_ctx["year_from"].id, horizonYears=2,
        ),
        admin,
    )
    # REGIONAL_ADMIN limité à region_a.
    reg_admin = await _make_admin_user(
        db_session, UserRole.REGIONAL_ADMIN,
        regionId=proj_ctx["region_a"].id,
    )
    results = await svc.get_projections(
        ProjectionFilters(scope=TransitionScope.REGIONAL, limit=1000),
        reg_admin,
    )
    region_ids = {r.entityId for r in results}
    assert proj_ctx["region_a"].id in region_ids
    # Region B NE doit PAS être visible pour le REGIONAL_ADMIN de A.
    assert proj_ctx["region_b"].id not in region_ids


# ===========================================================================
# 14. create_scenario avec demographic_growth personnalisé
# ===========================================================================
async def test_create_scenario_with_custom_demographic_growth(
    db_session: AsyncSession, proj_ctx: dict[str, Any],
) -> None:
    del proj_ctx  # unused
    admin = await _make_admin_user(db_session)
    svc = ProjectionService(db_session)
    scen = await svc.create_scenario(
        ProjectionScenarioCreate(
            name="OPTIMISTE_2030",
            description="Cabinet ministre — +10% maternelle.",
            demographicGrowthRate=Decimal("0.05"),
        ),
        admin,
    )
    assert scen.name == "OPTIMISTE_2030"
    assert scen.demographicGrowthRate == Decimal("0.0500")

    # Création par TEACHER refusée.
    teacher = await _make_admin_user(db_session, UserRole.TEACHER)
    with pytest.raises(ForbiddenError):
        await svc.create_scenario(
            ProjectionScenarioCreate(name="REFUSE_TEST"),
            teacher,
        )


# ===========================================================================
# 15. list_scenarios renvoie tous les scénarios visibles
# ===========================================================================
async def test_list_scenarios_returns_all_visible(
    db_session: AsyncSession, proj_ctx: dict[str, Any],
) -> None:
    del proj_ctx  # unused
    admin = await _make_admin_user(db_session)
    svc = ProjectionService(db_session)
    await svc.create_scenario(
        ProjectionScenarioCreate(
            name="SCENARIO_A",
            demographicGrowthRate=Decimal("0.03"),
        ),
        admin,
    )
    await svc.create_scenario(
        ProjectionScenarioCreate(
            name="SCENARIO_B",
            demographicGrowthRate=Decimal("0.01"),
        ),
        admin,
    )
    scenarios = await svc.list_scenarios()
    names = {s.name for s in scenarios}
    # BASELINE + nos 2 nouveaux.
    assert "BASELINE" in names
    assert "SCENARIO_A" in names
    assert "SCENARIO_B" in names
