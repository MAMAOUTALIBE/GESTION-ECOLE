"""Module 2D — Recommandation transferts enseignants.

Couvre :
1.  compute_ratio basique (cas nominal).
2.  compute_ratio retourne None pour 0 enseignant.
3.  classify_staffing table de seuils.
4.  expected_teachers avec ceil.
5.  priority score bonus same-prefecture.
6.  compute_snapshots persiste pour TOUTES les écoles.
7.  compute_snapshots refusé hors NATIONAL/MINISTRY.
8.  generate_recommendations apparie OVER avec UNDER/CRITICAL.
9.  generate priorise les transferts same-prefecture.
10. recommendations créées en statut PENDING.
11. review_recommendation met à jour statut + audit log.
12. review refusé hors REGIONAL_ADMIN+.
13. École CRITICAL crée une AnomalyDetection (hook Module 9).
14. Cockpit KPI inclut criticalStaffingSchools (hook Module 19).
15. list staffing respecte le scope territorial.
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
from app.modules.cockpit.enums import KpiKey
from app.modules.cockpit.service import CockpitService
from app.modules.projections.enums import (
    CRITICAL_RATIO,
    OVER_STAFFED_RATIO,
    STUDENTS_PER_TEACHER_NORM,
    UNDER_STAFFED_RATIO,
    RecommendationStatus,
    StaffingSeverity,
)
from app.modules.projections.models import (
    TeacherStaffingSnapshot,
    TeacherTransferRecommendation,
)
from app.modules.projections.schemas import (
    ReviewRecommendationRequest,
    StaffingFilters,
)
from app.modules.projections.service import TeacherStaffingService
from app.modules.projections.staffing import (
    classify_staffing,
    compute_priority_score,
    compute_ratio,
    expected_teachers,
)
from app.modules.workflow.models import AuditLog
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


async def _seed_school_with_population(
    db_session: AsyncSession,
    *,
    region_id: str,
    prefecture_id: str | None,
    sub_prefecture_id: str | None,
    students: int,
    teachers: int,
    classrooms_usable: int = 5,
) -> Any:
    """Crée une école + N students + N teachers."""
    school = await factories.SchoolFactory.create_async(
        regionId=region_id,
        prefectureId=prefecture_id,
        subPrefectureId=sub_prefecture_id,
        classroomsUsable=classrooms_usable,
        classroomsTotal=classrooms_usable,
    )
    for _ in range(students):
        await factories.StudentFactory.create_async(schoolId=school.id)
    for _ in range(teachers):
        await factories.TeacherFactory.create_async(schoolId=school.id)
    return school


@pytest_asyncio.fixture(loop_scope="session")
async def staffing_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Setup : 2 régions × 2 préfectures × écoles avec différents ratios.

    Architecture de test :
    * region_a / prefecture_a :
        - school_over_a : 100 students, 10 teachers → ratio 10 (OVER_STAFFED)
        - school_crit_a : 200 students, 2 teachers → ratio 100 (CRITICAL)
    * region_b / prefecture_b :
        - school_under_b : 100 students, 2 teachers → ratio 50 (ADEQUATE
          en réalité — borne haute ADEQUATE) ; on l'utilise pour tests
          de scope.
    """
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

    # Région A — même préfecture : un sur-doté + un critique → bonne paire.
    school_over_a = await _seed_school_with_population(
        db_session,
        region_id=region_a.id,
        prefecture_id=pref_a.id,
        sub_prefecture_id=sub_a.id,
        students=100,
        teachers=10,  # ratio 10 → OVER_STAFFED
    )
    school_crit_a = await _seed_school_with_population(
        db_session,
        region_id=region_a.id,
        prefecture_id=pref_a.id,
        sub_prefecture_id=sub_a.id,
        students=200,
        teachers=2,  # ratio 100 → CRITICAL
    )

    # Région B — école "isolée" pour tests de scope (ratio adéquat).
    school_b = await _seed_school_with_population(
        db_session,
        region_id=region_b.id,
        prefecture_id=pref_b.id,
        sub_prefecture_id=sub_b.id,
        students=40,
        teachers=1,  # ratio 40 → ADEQUATE
    )

    year = await _make_school_year(
        db_session, year=2025, name="YEAR-2D", is_active=True,
    )

    return {
        "region_a": region_a,
        "region_b": region_b,
        "prefecture_a": pref_a,
        "prefecture_b": pref_b,
        "school_over_a": school_over_a,
        "school_crit_a": school_crit_a,
        "school_b": school_b,
        "year": year,
    }


# ===========================================================================
# 1. compute_ratio basique
# ===========================================================================
def test_compute_ratio_basic() -> None:
    """100 élèves / 4 enseignants = 25.00."""
    r = compute_ratio(100, 4)
    assert r == Decimal("25.00")
    r2 = compute_ratio(0, 5)
    assert r2 == Decimal("0.00")
    # 200/3 = 66.666... → arrondi 66.67 (HALF_UP).
    r3 = compute_ratio(200, 3)
    assert r3 == Decimal("66.67")


# ===========================================================================
# 2. compute_ratio retourne None pour 0 enseignant
# ===========================================================================
def test_compute_ratio_zero_teachers_returns_none() -> None:
    """teachers=0 → ratio None (pas de division par zéro)."""
    assert compute_ratio(100, 0) is None
    assert compute_ratio(0, 0) is None
    assert compute_ratio(100, -1) is None
    # students négatif → ValueError.
    with pytest.raises(ValueError):
        compute_ratio(-1, 5)


# ===========================================================================
# 3. classify_staffing table de seuils
# ===========================================================================
def test_classify_staffing_thresholds() -> None:
    """Table OVER_STAFFED / ADEQUATE / UNDER_STAFFED / CRITICAL."""
    # < 25 → OVER_STAFFED
    assert (
        classify_staffing(OVER_STAFFED_RATIO - Decimal("0.01"))
        == StaffingSeverity.OVER_STAFFED
    )
    assert classify_staffing(Decimal("10")) == StaffingSeverity.OVER_STAFFED
    # 25 borne incluse → ADEQUATE
    assert (
        classify_staffing(OVER_STAFFED_RATIO) == StaffingSeverity.ADEQUATE
    )
    assert classify_staffing(Decimal("40")) == StaffingSeverity.ADEQUATE
    # 50 borne incluse → ADEQUATE
    assert (
        classify_staffing(UNDER_STAFFED_RATIO) == StaffingSeverity.ADEQUATE
    )
    # > 50 → UNDER_STAFFED
    assert (
        classify_staffing(UNDER_STAFFED_RATIO + Decimal("0.01"))
        == StaffingSeverity.UNDER_STAFFED
    )
    assert (
        classify_staffing(Decimal("60")) == StaffingSeverity.UNDER_STAFFED
    )
    # 70 borne incluse → UNDER_STAFFED
    assert (
        classify_staffing(CRITICAL_RATIO) == StaffingSeverity.UNDER_STAFFED
    )
    # > 70 → CRITICAL
    assert (
        classify_staffing(CRITICAL_RATIO + Decimal("0.01"))
        == StaffingSeverity.CRITICAL
    )
    assert classify_staffing(Decimal("100")) == StaffingSeverity.CRITICAL
    # None → CRITICAL
    assert classify_staffing(None) == StaffingSeverity.CRITICAL


# ===========================================================================
# 4. expected_teachers avec ceil
# ===========================================================================
def test_expected_teachers_ceil() -> None:
    """ceil(students / norm)."""
    # 40/40 = 1 pile.
    assert expected_teachers(40) == 1
    # 41 → ceil = 2.
    assert expected_teachers(41) == 2
    # 80 → 2.
    assert expected_teachers(80) == 2
    # 81 → 3.
    assert expected_teachers(81) == 3
    # 0 → 0.
    assert expected_teachers(0) == 0
    # Norme custom.
    assert expected_teachers(100, norm=25) == 4
    # Norme invalide.
    with pytest.raises(ValueError):
        expected_teachers(40, norm=0)
    with pytest.raises(ValueError):
        expected_teachers(-1)


# ===========================================================================
# 5. priority score bonus same-prefecture
# ===========================================================================
def test_priority_score_same_prefecture_higher() -> None:
    """Score same-pref = score diff-pref + 20."""
    donor = Decimal("10.00")
    receiver = Decimal("80.00")
    score_diff = compute_priority_score(donor, receiver, False)
    score_same = compute_priority_score(donor, receiver, True)
    assert score_same == score_diff + Decimal("20")
    # Cas avec None.
    s = compute_priority_score(None, Decimal("60"), True)
    # base = 60 - 0 = 60 ; + bonus 20 → 80.
    assert s == Decimal("80.00")


# ===========================================================================
# 6. compute_snapshots persiste pour toutes les écoles
# ===========================================================================
async def test_compute_snapshots_persists_for_all_schools(
    db_session: AsyncSession, staffing_ctx: dict[str, Any],
) -> None:
    """Un snapshot par école active (status APPROVED)."""
    admin = await _make_admin_user(db_session)
    svc = TeacherStaffingService(db_session)
    resp = await svc.compute_staffing_snapshots(
        staffing_ctx["year"].id, admin,
    )
    # 3 écoles dans le fixture.
    assert resp.snapshots == 3

    rows = (
        await db_session.execute(
            select(TeacherStaffingSnapshot)
            .where(
                TeacherStaffingSnapshot.schoolYearId
                == staffing_ctx["year"].id,
            )
        )
    ).scalars().all()
    by_school = {r.schoolId: r for r in rows}
    # School OVER_STAFFED A : 100 students, 10 teachers → ratio 10.
    over_a = by_school[staffing_ctx["school_over_a"].id]
    assert over_a.studentsCount == 100
    assert over_a.teachersCount == 10
    assert over_a.ratio == Decimal("10.00")
    assert over_a.severity == StaffingSeverity.OVER_STAFFED
    # expected = ceil(100/40) = 3 ; gap = 3 - 10 = -7 (sur-doté de 7).
    assert over_a.expectedTeachers == 3
    assert over_a.gap == -7

    # School CRITICAL A : 200 students, 2 teachers → ratio 100.
    crit_a = by_school[staffing_ctx["school_crit_a"].id]
    assert crit_a.ratio == Decimal("100.00")
    assert crit_a.severity == StaffingSeverity.CRITICAL
    # expected = ceil(200/40) = 5 ; gap = 5 - 2 = 3 (besoin de 3).
    assert crit_a.expectedTeachers == 5
    assert crit_a.gap == 3


# ===========================================================================
# 7. compute_snapshots refusé hors NATIONAL/MINISTRY
# ===========================================================================
async def test_compute_snapshots_requires_admin(
    db_session: AsyncSession, staffing_ctx: dict[str, Any],
) -> None:
    teacher = await _make_admin_user(db_session, UserRole.TEACHER)
    svc = TeacherStaffingService(db_session)
    with pytest.raises(ForbiddenError):
        await svc.compute_staffing_snapshots(
            staffing_ctx["year"].id, teacher,
        )


# ===========================================================================
# 8. generate_recommendations pair OVER ↔ UNDER/CRITICAL
# ===========================================================================
async def test_generate_recommendations_pairs_overstaffed_with_understaffed(
    db_session: AsyncSession, staffing_ctx: dict[str, Any],
) -> None:
    """OVER_STAFFED A ↔ CRITICAL A (même région, même préfecture)."""
    admin = await _make_admin_user(db_session)
    svc = TeacherStaffingService(db_session)
    await svc.compute_staffing_snapshots(staffing_ctx["year"].id, admin)
    resp = await svc.generate_recommendations(
        staffing_ctx["year"].id, admin,
    )
    assert resp.recommendations >= 1

    recs = (
        await db_session.execute(
            select(TeacherTransferRecommendation)
            .where(
                TeacherTransferRecommendation.schoolYearId
                == staffing_ctx["year"].id,
            )
        )
    ).scalars().all()
    # Au moins une recommandation pour la paire over_a → crit_a.
    paired = [
        r for r in recs
        if r.fromSchoolId == staffing_ctx["school_over_a"].id
        and r.toSchoolId == staffing_ctx["school_crit_a"].id
    ]
    assert len(paired) == 1
    rec = paired[0]
    assert rec.transfersSuggested >= 1
    assert rec.regionId == staffing_ctx["region_a"].id
    # School_over_a sur-doté de 7 ; school_crit_a a besoin de 3 → min = 3.
    assert rec.transfersSuggested == 3


# ===========================================================================
# 9. generate priorise les transferts same-prefecture
# ===========================================================================
async def test_generate_prefers_same_prefecture_transfers(
    db_session: AsyncSession, staffing_ctx: dict[str, Any],
) -> None:
    """Bonus +20 visible sur le priorityScore quand même préfecture."""
    admin = await _make_admin_user(db_session)
    svc = TeacherStaffingService(db_session)
    await svc.compute_staffing_snapshots(staffing_ctx["year"].id, admin)
    await svc.generate_recommendations(staffing_ctx["year"].id, admin)

    recs = (
        await db_session.execute(
            select(TeacherTransferRecommendation)
            .where(
                TeacherTransferRecommendation.fromSchoolId
                == staffing_ctx["school_over_a"].id,
            )
        )
    ).scalars().all()
    assert len(recs) >= 1
    rec = recs[0]
    # same-prefecture → prefectureId renseigné (donneur même préfecture).
    assert rec.prefectureId == staffing_ctx["prefecture_a"].id
    # Score doit inclure le bonus +20 (score positif élevé).
    assert rec.priorityScore >= Decimal("20")


# ===========================================================================
# 10. recommendations créées avec status PENDING
# ===========================================================================
async def test_generate_creates_recommendations_with_status_pending(
    db_session: AsyncSession, staffing_ctx: dict[str, Any],
) -> None:
    admin = await _make_admin_user(db_session)
    svc = TeacherStaffingService(db_session)
    await svc.compute_staffing_snapshots(staffing_ctx["year"].id, admin)
    await svc.generate_recommendations(staffing_ctx["year"].id, admin)

    recs = (
        await db_session.execute(
            select(TeacherTransferRecommendation)
        )
    ).scalars().all()
    assert len(recs) >= 1
    for r in recs:
        assert r.status == RecommendationStatus.PENDING
        assert r.reviewedById is None
        assert r.reviewedAt is None


# ===========================================================================
# 11. review_recommendation met à jour statut + audit
# ===========================================================================
async def test_review_recommendation_updates_status_and_audits(
    db_session: AsyncSession, staffing_ctx: dict[str, Any],
) -> None:
    admin = await _make_admin_user(db_session)
    svc = TeacherStaffingService(db_session)
    await svc.compute_staffing_snapshots(staffing_ctx["year"].id, admin)
    await svc.generate_recommendations(staffing_ctx["year"].id, admin)

    rec = (
        await db_session.execute(
            select(TeacherTransferRecommendation).limit(1)
        )
    ).scalars().one()
    result = await svc.review_recommendation(
        rec.id,
        ReviewRecommendationRequest(
            status=RecommendationStatus.ACCEPTED,
            reviewNote="OK, transfert acte en CR du 24/05",
        ),
        admin,
    )
    assert result.status == RecommendationStatus.ACCEPTED
    assert result.reviewedById == admin.id
    assert result.reviewedAt is not None

    # Audit log présent.
    logs = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action
                == "REVIEW_TEACHER_TRANSFER_RECOMMENDATION",
                AuditLog.entityId == rec.id,
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    log = logs[0]
    assert log.metadata_["newStatus"] == RecommendationStatus.ACCEPTED.value
    assert log.metadata_["previousStatus"] == (
        RecommendationStatus.PENDING.value
    )


# ===========================================================================
# 12. review refusé hors REGIONAL_ADMIN+
# ===========================================================================
async def test_review_requires_regional_admin(
    db_session: AsyncSession, staffing_ctx: dict[str, Any],
) -> None:
    admin = await _make_admin_user(db_session)
    svc = TeacherStaffingService(db_session)
    await svc.compute_staffing_snapshots(staffing_ctx["year"].id, admin)
    await svc.generate_recommendations(staffing_ctx["year"].id, admin)

    rec = (
        await db_session.execute(
            select(TeacherTransferRecommendation).limit(1)
        )
    ).scalars().one()

    teacher = await _make_admin_user(db_session, UserRole.TEACHER)
    with pytest.raises(ForbiddenError):
        await svc.review_recommendation(
            rec.id,
            ReviewRecommendationRequest(
                status=RecommendationStatus.REJECTED,
            ),
            teacher,
        )


# ===========================================================================
# 13. École CRITICAL crée AnomalyDetection (hook Module 9)
# ===========================================================================
async def test_critical_school_creates_anomaly_record(
    db_session: AsyncSession, staffing_ctx: dict[str, Any],
) -> None:
    admin = await _make_admin_user(db_session)
    svc = TeacherStaffingService(db_session)
    await svc.compute_staffing_snapshots(staffing_ctx["year"].id, admin)

    anomalies = (
        await db_session.execute(
            select(AnomalyDetection)
            .where(
                AnomalyDetection.type
                == AnomalyType.CRITICAL_TEACHER_SHORTAGE,
            )
        )
    ).scalars().all()
    assert len(anomalies) >= 1
    # Au moins une anomalie concerne school_crit_a.
    crit_a_anomalies = [
        a for a in anomalies
        if a.entityId == staffing_ctx["school_crit_a"].id
    ]
    assert len(crit_a_anomalies) >= 1
    assert crit_a_anomalies[0].entityType == "School"


# ===========================================================================
# 14. Cockpit KPI inclut criticalStaffingSchools (hook Module 19)
# ===========================================================================
async def test_cockpit_kpis_includes_critical_staffing_count(
    db_session: AsyncSession, staffing_ctx: dict[str, Any],
) -> None:
    admin = await _make_admin_user(db_session)
    svc = TeacherStaffingService(db_session)
    await svc.compute_staffing_snapshots(staffing_ctx["year"].id, admin)

    cs = CockpitService(db_session)
    response = await cs.get_national_kpis()
    assert response.criticalStaffingSchools >= 1
    # Items dict doit aussi exposer la clé.
    assert KpiKey.SCHOOLS_CRITICAL_STAFFING_COUNT.value in response.items
    assert (
        response.items[KpiKey.SCHOOLS_CRITICAL_STAFFING_COUNT.value]
        >= 1.0
    )


# ===========================================================================
# 15. list staffing respecte le scope territorial
# ===========================================================================
async def test_list_staffing_respects_territorial_scope(
    db_session: AsyncSession, staffing_ctx: dict[str, Any],
) -> None:
    admin = await _make_admin_user(db_session)
    svc = TeacherStaffingService(db_session)
    await svc.compute_staffing_snapshots(staffing_ctx["year"].id, admin)

    reg_admin_a = await _make_admin_user(
        db_session, UserRole.REGIONAL_ADMIN,
        regionId=staffing_ctx["region_a"].id,
    )
    results = await svc.list_staffing(
        StaffingFilters(
            schoolYearId=staffing_ctx["year"].id,
            limit=1000,
        ),
        reg_admin_a,
    )
    school_ids = {r.schoolId for r in results}
    # Voit les 2 écoles de region_a, pas celle de region_b.
    assert staffing_ctx["school_over_a"].id in school_ids
    assert staffing_ctx["school_crit_a"].id in school_ids
    assert staffing_ctx["school_b"].id not in school_ids
