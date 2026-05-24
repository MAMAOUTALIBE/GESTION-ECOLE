"""Module 3B — Simulateur what-if de réorganisation du réseau scolaire.

Couvre :

1.  apply_operations CREATE_SCHOOL ajoute une école virtuelle.
2.  apply_operations CLOSE_SCHOOL retire une école.
3.  apply_operations MERGE_SCHOOLS combine les capacités.
4.  apply_operations sur un id inconnu lève ValueError.
5.  compute_impact coverage augmente avec un CREATE.
6.  compute_impact saturation baisse avec un CREATE en zone critique.
7.  compute_impact redistributedStudents sur un CLOSE.
8.  create_scenario persiste avec status DRAFT.
9.  compute_scenario fixe status COMPUTED et impactJson rempli.
10. create_scenario refusé hors NATIONAL/MINISTRY/REGIONAL_ADMIN.
11. list_scenarios applique scope visibilité (created_by).
12. archive_scenario fixe status ARCHIVED.
13. compute idempotent — un re-compute écrase impactJson.
14. MERGE validates au moins 2 écoles sources.

Tous les tests utilisent le helper interne (pas de HTTP layer ici — le
router est trivial, on couvre la logique).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.modules.academics.models import SchoolYear
from app.modules.auth.models import User
from app.modules.simulator.enums import OperationType, ScenarioStatus
from app.modules.simulator.models import SimulationScenario
from app.modules.simulator.schemas import (
    CloseSchoolOp,
    CreateSchoolOp,
    MergeSchoolsOp,
    ScenarioCreate,
)
from app.modules.simulator.service import SimulatorService
from app.modules.simulator.simulator import (
    VirtualSchool,
    apply_operations,
    compute_impact,
)
from app.shared.base import generate_cuid
from app.shared.enums import AcademicPeriodType, UserRole
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _make_school_year(
    session: AsyncSession,
    *,
    name: str | None = None,
    year: int = 2025,
) -> SchoolYear:
    sy = SchoolYear(
        id=generate_cuid(),
        name=name or f"YEAR-3B-{generate_cuid()[:6]}",
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


def _vs(
    id_: str,
    *,
    capacity: int = 500,
    students: int = 250,
    lat: float | None = 9.5,
    lon: float | None = -13.5,
    sub_pref: str | None = "sub-a",
    is_virtual: bool = False,
) -> VirtualSchool:
    """Helper : crée une VirtualSchool de test."""
    return VirtualSchool(
        id=id_,
        name=f"School {id_}",
        lat=lat,
        lon=lon,
        capacity=capacity,
        studentsCount=students,
        subPrefectureId=sub_pref,
        isVirtual=is_virtual,
    )


# ===========================================================================
# 1. apply_operations CREATE_SCHOOL ajoute une école virtuelle
# ===========================================================================
def test_apply_create_school_adds_virtual_school() -> None:
    baseline = [_vs("s1"), _vs("s2")]
    op = CreateSchoolOp(
        type=OperationType.CREATE_SCHOOL,
        name="Nouvelle école",
        lat=9.6,
        lon=-13.6,
        capacity=300,
        subPrefectureId="sub-a",
    )
    result = apply_operations(baseline, [op])
    assert len(result) == 3
    virtuals = [s for s in result if s.isVirtual]
    assert len(virtuals) == 1
    assert virtuals[0].name == "Nouvelle école"
    assert virtuals[0].capacity == 300
    assert virtuals[0].studentsCount == 0
    # Baseline reste inchangée (isolation).
    assert all(not s.isVirtual for s in baseline)


# ===========================================================================
# 2. apply_operations CLOSE_SCHOOL retire l'école
# ===========================================================================
def test_apply_close_school_removes_school() -> None:
    baseline = [_vs("s1"), _vs("s2"), _vs("s3")]
    op = CloseSchoolOp(
        type=OperationType.CLOSE_SCHOOL, schoolId="s2",
    )
    result = apply_operations(baseline, [op])
    ids = {s.id for s in result}
    assert ids == {"s1", "s3"}


# ===========================================================================
# 3. apply_operations MERGE_SCHOOLS combine les capacités
# ===========================================================================
def test_apply_merge_schools_combines_capacity() -> None:
    baseline = [
        _vs("s1", capacity=300, students=100),
        _vs("s2", capacity=200, students=150),
        _vs("s3", capacity=100, students=50),
    ]
    op = MergeSchoolsOp(
        type=OperationType.MERGE_SCHOOLS,
        sourceSchoolIds=["s1", "s2"],
        targetName="École Fusion",
        lat=9.5,
        lon=-13.5,
        subPrefectureId="sub-a",
    )
    result = apply_operations(baseline, [op])
    # s1 + s2 retirés, s3 + 1 nouvelle = 2.
    assert len(result) == 2
    fused = [s for s in result if s.isVirtual]
    assert len(fused) == 1
    assert fused[0].capacity == 500  # 300 + 200
    assert fused[0].studentsCount == 250  # 100 + 150
    assert fused[0].name == "École Fusion"
    assert set(fused[0].mergedFrom) == {"s1", "s2"}


# ===========================================================================
# 4. apply_operations sur un id inconnu lève ValueError
# ===========================================================================
def test_apply_unknown_school_id_raises() -> None:
    baseline = [_vs("s1")]
    op_close = CloseSchoolOp(
        type=OperationType.CLOSE_SCHOOL, schoolId="does-not-exist",
    )
    with pytest.raises(ValueError, match="introuvable"):
        apply_operations(baseline, [op_close])

    op_merge = MergeSchoolsOp(
        type=OperationType.MERGE_SCHOOLS,
        sourceSchoolIds=["s1", "ghost"],
        targetName="X",
        lat=9.5,
        lon=-13.5,
    )
    with pytest.raises(ValueError, match="introuvable"):
        apply_operations(baseline, [op_merge])


# ===========================================================================
# 5. compute_impact coverage augmente avec un CREATE
# ===========================================================================
def test_compute_impact_coverage_increases_with_create() -> None:
    baseline = [_vs("s1"), _vs("s2")]
    op = CreateSchoolOp(
        type=OperationType.CREATE_SCHOOL,
        name="Nouvelle",
        lat=9.6,
        lon=-13.6,
        capacity=300,
    )
    simulated = apply_operations(baseline, [op])
    report = compute_impact(baseline, simulated)
    assert report.coverage.beforeCount == 2
    assert report.coverage.afterCount == 3
    # 1/2 = +50.00 %.
    assert str(report.coverage.deltaPct) == "50.00"


# ===========================================================================
# 6. compute_impact saturation baisse avec un CREATE en zone critique
# ===========================================================================
def test_compute_impact_saturation_decreases_with_create_in_critical_zone() -> None:
    # 2 écoles critiques : 500 capacity, 700 students (saturation 140 %)
    baseline = [
        _vs("s1", capacity=500, students=700),
        _vs("s2", capacity=500, students=700),
    ]
    # On ajoute une école avec 1000 capacity vide (0 students) →
    # la moyenne pondérée baisse.
    op = CreateSchoolOp(
        type=OperationType.CREATE_SCHOOL,
        name="Décharge",
        lat=9.6,
        lon=-13.6,
        capacity=1000,
    )
    simulated = apply_operations(baseline, [op])
    report = compute_impact(baseline, simulated)
    # Avant : 2 écoles critiques (saturation = 140%).
    assert report.saturation.criticalSchoolsBefore == 2
    # Après : 2 écoles critiques (s1/s2) ; la nouvelle n'est pas
    # critique (sat = 0).
    assert report.saturation.criticalSchoolsAfter == 2
    # La moyenne baisse : (140 + 140) / 2 = 140 vs (140 + 140 + 0) / 3 ≈ 93.33.
    assert report.saturation.beforeAvg is not None
    assert report.saturation.afterAvg is not None
    assert report.saturation.afterAvg < report.saturation.beforeAvg


# ===========================================================================
# 7. compute_impact redistributedStudents sur un CLOSE
# ===========================================================================
def test_compute_impact_redistributed_students_on_close() -> None:
    baseline = [
        _vs("s1", students=120),
        _vs("s2", students=80),
    ]
    op = CloseSchoolOp(
        type=OperationType.CLOSE_SCHOOL, schoolId="s2",
    )
    simulated = apply_operations(baseline, [op])
    report = compute_impact(baseline, simulated)
    # s2 (80 élèves) doit être redistribuée.
    assert report.redistributedStudents == 80


# ===========================================================================
# 8. create_scenario persiste avec status DRAFT
# ===========================================================================
@pytest_asyncio.fixture(loop_scope="session")
async def sim_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Setup : factories + 2 écoles APPROVED + 1 année."""
    factories.bind(db_session)
    region = await factories.RegionFactory.create_async()
    pref = await factories.PrefectureFactory.create_async(
        regionId=region.id,
    )
    sub_pref = await factories.SubPrefectureFactory.create_async(
        regionId=region.id, prefectureId=pref.id,
    )
    school_a = await factories.SchoolFactory.create_async(
        regionId=region.id,
        prefectureId=pref.id,
        subPrefectureId=sub_pref.id,
        classroomsUsable=10,
        classroomsTotal=10,
        latitude=9.5,
        longitude=-13.5,
    )
    school_b = await factories.SchoolFactory.create_async(
        regionId=region.id,
        prefectureId=pref.id,
        subPrefectureId=sub_pref.id,
        classroomsUsable=5,
        classroomsTotal=5,
        latitude=9.6,
        longitude=-13.6,
    )
    year = await _make_school_year(db_session)
    admin = await _make_user(db_session, role=UserRole.NATIONAL_ADMIN)
    regional = await _make_user(
        db_session,
        role=UserRole.REGIONAL_ADMIN,
        regionId=region.id,
    )
    teacher = await _make_user(db_session, role=UserRole.TEACHER)
    return {
        "region": region,
        "prefecture": pref,
        "sub_pref": sub_pref,
        "school_a": school_a,
        "school_b": school_b,
        "year": year,
        "admin": admin,
        "regional": regional,
        "teacher": teacher,
    }


async def test_create_scenario_persists_with_status_draft(
    db_session: AsyncSession,
    sim_ctx: dict[str, Any],
) -> None:
    svc = SimulatorService(db_session)
    dto = ScenarioCreate(
        name="Test DRAFT",
        description="Test scenario",
        baselineSchoolYearId=sim_ctx["year"].id,
        operations=[
            CreateSchoolOp(
                type=OperationType.CREATE_SCHOOL,
                name="Nouvelle",
                lat=9.7,
                lon=-13.7,
                capacity=200,
            ),
        ],
    )
    read = await svc.create_scenario(dto, sim_ctx["admin"])
    assert read.status == ScenarioStatus.DRAFT
    assert read.impactJson is None
    assert read.computedAt is None
    # Vérifie persistance.
    row = (
        await db_session.execute(
            select(SimulationScenario)
            .where(SimulationScenario.id == read.id)
        )
    ).scalars().one()
    assert row.status == ScenarioStatus.DRAFT
    # Le payload JSON contient bien notre op.
    ops = row.scenarioJson["operations"]
    assert len(ops) == 1
    assert ops[0]["type"] == "CREATE_SCHOOL"


# ===========================================================================
# 9. compute_scenario fixe status COMPUTED et impactJson rempli
# ===========================================================================
async def test_compute_scenario_sets_status_computed_and_impact_json(
    db_session: AsyncSession,
    sim_ctx: dict[str, Any],
) -> None:
    svc = SimulatorService(db_session)
    dto = ScenarioCreate(
        name="Test COMPUTE",
        baselineSchoolYearId=sim_ctx["year"].id,
        operations=[
            CloseSchoolOp(
                type=OperationType.CLOSE_SCHOOL,
                schoolId=sim_ctx["school_b"].id,
            ),
        ],
    )
    created = await svc.create_scenario(dto, sim_ctx["admin"])
    report = await svc.compute_scenario(created.id, sim_ctx["admin"])

    assert report.coverage.beforeCount == 2
    assert report.coverage.afterCount == 1
    # Verify persistance.
    row = (
        await db_session.execute(
            select(SimulationScenario)
            .where(SimulationScenario.id == created.id)
        )
    ).scalars().one()
    assert row.status == ScenarioStatus.COMPUTED
    assert row.impactJson is not None
    assert "coverage" in row.impactJson
    assert row.computedAt is not None


# ===========================================================================
# 10. create_scenario refusé hors NATIONAL/MINISTRY/REGIONAL_ADMIN
# ===========================================================================
async def test_create_scenario_requires_role(
    db_session: AsyncSession,
    sim_ctx: dict[str, Any],
) -> None:
    svc = SimulatorService(db_session)
    dto = ScenarioCreate(
        name="Forbidden",
        baselineSchoolYearId=sim_ctx["year"].id,
        operations=[
            CloseSchoolOp(
                type=OperationType.CLOSE_SCHOOL,
                schoolId=sim_ctx["school_b"].id,
            ),
        ],
    )
    with pytest.raises(ForbiddenError):
        await svc.create_scenario(dto, sim_ctx["teacher"])
    # REGIONAL_ADMIN doit passer.
    read = await svc.create_scenario(dto, sim_ctx["regional"])
    assert read.status == ScenarioStatus.DRAFT


# ===========================================================================
# 11. list_scenarios applique scope visibilité
# ===========================================================================
async def test_list_scenarios_returns_only_visible_to_user(
    db_session: AsyncSession,
    sim_ctx: dict[str, Any],
) -> None:
    svc = SimulatorService(db_session)
    # Admin crée un scénario.
    admin_dto = ScenarioCreate(
        name="Admin scenario",
        baselineSchoolYearId=sim_ctx["year"].id,
        operations=[
            CloseSchoolOp(
                type=OperationType.CLOSE_SCHOOL,
                schoolId=sim_ctx["school_a"].id,
            ),
        ],
    )
    admin_scen = await svc.create_scenario(admin_dto, sim_ctx["admin"])
    # Regional crée son scénario.
    regional_dto = ScenarioCreate(
        name="Regional scenario",
        baselineSchoolYearId=sim_ctx["year"].id,
        operations=[
            CloseSchoolOp(
                type=OperationType.CLOSE_SCHOOL,
                schoolId=sim_ctx["school_b"].id,
            ),
        ],
    )
    regional_scen = await svc.create_scenario(
        regional_dto, sim_ctx["regional"],
    )

    # Admin (NATIONAL) voit les deux.
    admin_list = await svc.list_scenarios(sim_ctx["admin"])
    admin_ids = {s.id for s in admin_list}
    assert admin_scen.id in admin_ids
    assert regional_scen.id in admin_ids

    # Regional voit seulement le sien.
    regional_list = await svc.list_scenarios(sim_ctx["regional"])
    regional_ids = {s.id for s in regional_list}
    assert regional_scen.id in regional_ids
    assert admin_scen.id not in regional_ids


# ===========================================================================
# 12. archive_scenario fixe status ARCHIVED
# ===========================================================================
async def test_archive_scenario_sets_status_archived(
    db_session: AsyncSession,
    sim_ctx: dict[str, Any],
) -> None:
    svc = SimulatorService(db_session)
    dto = ScenarioCreate(
        name="To archive",
        baselineSchoolYearId=sim_ctx["year"].id,
        operations=[
            CreateSchoolOp(
                type=OperationType.CREATE_SCHOOL,
                name="X",
                lat=9.5,
                lon=-13.5,
                capacity=100,
            ),
        ],
    )
    created = await svc.create_scenario(dto, sim_ctx["admin"])
    archived = await svc.archive_scenario(created.id, sim_ctx["admin"])
    assert archived.status == ScenarioStatus.ARCHIVED
    # Pas listé par défaut.
    listing = await svc.list_scenarios(sim_ctx["admin"])
    assert created.id not in {s.id for s in listing}


# ===========================================================================
# 13. compute idempotent — un re-compute écrase impactJson
# ===========================================================================
async def test_compute_idempotent_overwrites_impact(
    db_session: AsyncSession,
    sim_ctx: dict[str, Any],
) -> None:
    svc = SimulatorService(db_session)
    dto = ScenarioCreate(
        name="Idempotent test",
        baselineSchoolYearId=sim_ctx["year"].id,
        operations=[
            CloseSchoolOp(
                type=OperationType.CLOSE_SCHOOL,
                schoolId=sim_ctx["school_a"].id,
            ),
        ],
    )
    created = await svc.create_scenario(dto, sim_ctx["admin"])
    first = await svc.compute_scenario(created.id, sim_ctx["admin"])
    row1 = (
        await db_session.execute(
            select(SimulationScenario)
            .where(SimulationScenario.id == created.id)
        )
    ).scalars().one()
    first_computed_at = row1.computedAt

    # Re-compute. Doit réussir (idempotent), même résultat coverage.
    second = await svc.compute_scenario(created.id, sim_ctx["admin"])
    assert second.coverage.beforeCount == first.coverage.beforeCount
    assert second.coverage.afterCount == first.coverage.afterCount

    # computedAt mis à jour (ou identique). Au minimum impactJson reste
    # cohérent.
    row2 = (
        await db_session.execute(
            select(SimulationScenario)
            .where(SimulationScenario.id == created.id)
        )
    ).scalars().one()
    assert row2.status == ScenarioStatus.COMPUTED
    assert row2.impactJson is not None
    # computedAt est mis à jour (>= au premier).
    assert row2.computedAt is not None
    assert first_computed_at is not None
    assert row2.computedAt >= first_computed_at


# ===========================================================================
# 14. MERGE validates au moins 2 écoles sources
# ===========================================================================
def test_merge_validates_at_least_two_source_schools() -> None:
    # Pydantic rejette à la construction (min_length=2).
    with pytest.raises(Exception) as exc:  # noqa: BLE001
        MergeSchoolsOp(
            type=OperationType.MERGE_SCHOOLS,
            sourceSchoolIds=["s1"],
            targetName="One",
            lat=9.5,
            lon=-13.5,
        )
    # Le message Pydantic mentionne la contrainte ; assertion robuste.
    msg = str(exc.value).lower()
    assert "at least 2" in msg or "min_length" in msg or "too_short" in msg


# ===========================================================================
# Bonus — distance impact reste cohérente quand on a des centroids
# (test utilitaire qui vérifie le calcul haversine).
# ===========================================================================
def test_compute_impact_distance_with_centroids() -> None:
    """Vérifie le calcul distance avec des centroids fournis."""
    baseline = [
        _vs("s1", students=100, lat=9.5, lon=-13.5, sub_pref="sub-a"),
        _vs("s2", students=100, lat=9.5, lon=-13.5, sub_pref="sub-a"),
    ]
    # Si on ne fournit aucun centroid, la distance est None.
    report_no_centroid = compute_impact(baseline, baseline)
    assert report_no_centroid.distance.beforeKmMean is None
    # Avec un centroid identique aux écoles, distance ≈ 0.
    report_with_centroid = compute_impact(
        baseline, baseline,
        sub_prefecture_centroids={"sub-a": (9.5, -13.5)},
    )
    assert report_with_centroid.distance.beforeKmMean is not None
    assert float(report_with_centroid.distance.beforeKmMean) < 0.5
