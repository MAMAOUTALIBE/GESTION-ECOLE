"""Module 2A — Taux de transition par cohortes (IIPE-UNESCO).

Couvre :
1.  compute_rate cas basique.
2.  compute_rate count_from = 0 → None.
3.  compute_rate rate > 2 → isOutlier True.
4.  compute_transitions persiste rates pour toutes régions × paires.
5.  compute_transitions skip silencieux si pas de SchoolYear successeur.
6.  compute_transitions refusé hors NATIONAL/MINISTRY (TEACHER → 403).
7.  Rate NATIONAL = somme pondérée (pas moyenne simple).
8.  Rates désagrégés par genre (FEMALE / MALE).
9.  Rates désagrégés par région.
10. list_rates filtre par région.
11. list_rates respecte le scope territorial (REGIONAL_ADMIN).
12. get_outliers ne renvoie que les rates flaggés.
13. Hook Module 9 — anomalies TRANSITION_RATE_OUTLIER créées.
14. Re-compute idempotent : ré-exécution remplace l'ancien rate.
15. compute_transitions renvoie counts computed + outliers.
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
from app.modules.anomalies.enums import AnomalyStatus, AnomalyType
from app.modules.anomalies.models import AnomalyDetection
from app.modules.auth.models import User
from app.modules.enrollment.enums import (
    EnrollmentClassLevel,
    EnrollmentSource,
)
from app.modules.enrollment.models import Enrollment
from app.modules.projections.enums import TransitionScope
from app.modules.projections.models import TransitionRate
from app.modules.projections.schemas import TransitionRateFilters
from app.modules.projections.service import TransitionRateService
from app.modules.projections.transitions import (
    LEVEL_PAIRS,
    compute_rate,
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


@pytest_asyncio.fixture(loop_scope="session")
async def trans_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Setup : 2 régions × 2 écoles + 2 années (year_from + year_to)."""
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
        db_session, year=2024, name="YEAR-FROM", is_active=False,
    )
    year_to = await _make_school_year(
        db_session, year=2025, name="YEAR-TO", is_active=True,
    )
    return {
        "region_a": region_a,
        "region_b": region_b,
        "school_a": school_a,
        "school_b": school_b,
        "year_from": year_from,
        "year_to": year_to,
    }


# ===========================================================================
# 1. compute_rate basique
# ===========================================================================
def test_compute_rate_basic_division() -> None:
    """80 (count_to) / 100 (count_from) = 0.8000 — pas outlier."""
    rate, is_outlier = compute_rate(100, 80)
    assert rate is not None
    assert isinstance(rate, Decimal)
    assert rate == Decimal("0.8000")
    assert is_outlier is False


# ===========================================================================
# 2. compute_rate count_from = 0 → None
# ===========================================================================
def test_compute_rate_zero_denominator_returns_none() -> None:
    """count_from = 0 → rate None, pas outlier."""
    rate, is_outlier = compute_rate(0, 50)
    assert rate is None
    assert is_outlier is False
    # Cas double 0 aussi.
    rate2, _ = compute_rate(0, 0)
    assert rate2 is None


# ===========================================================================
# 3. compute_rate rate > 2 → isOutlier True
# ===========================================================================
def test_compute_rate_high_value_flagged_outlier() -> None:
    """count_to >> count_from → rate > 2 → isOutlier True."""
    rate, is_outlier = compute_rate(10, 25)
    assert rate == Decimal("2.5000")
    assert is_outlier is True
    # Cas limite : rate exactement 2 → PAS outlier.
    rate, is_outlier = compute_rate(10, 20)
    assert rate == Decimal("2.0000")
    assert is_outlier is False


# ===========================================================================
# 4. compute_transitions persiste pour toutes régions × paires
# ===========================================================================
async def test_compute_transitions_persists_for_all_regions_and_pairs(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    # Seed un enrollment minimal (CP1 → CP2) pour les 2 régions.
    for school_id, region_id in (
        (trans_ctx["school_a"].id, trans_ctx["region_a"].id),
        (trans_ctx["school_b"].id, trans_ctx["region_b"].id),
    ):
        del region_id  # unused — c'est l'école qui porte la liaison.
        _seed_enrollment(
            db_session,
            school_year_id=trans_ctx["year_from"].id,
            school_id=school_id,
            class_level=EnrollmentClassLevel.CP1,
            gender=Gender.FEMALE,
            count=100,
        )
        _seed_enrollment(
            db_session,
            school_year_id=trans_ctx["year_to"].id,
            school_id=school_id,
            class_level=EnrollmentClassLevel.CP2,
            gender=Gender.FEMALE,
            count=80,
        )
        _seed_enrollment(
            db_session,
            school_year_id=trans_ctx["year_from"].id,
            school_id=school_id,
            class_level=EnrollmentClassLevel.CP1,
            gender=Gender.MALE,
            count=100,
        )
        _seed_enrollment(
            db_session,
            school_year_id=trans_ctx["year_to"].id,
            school_id=school_id,
            class_level=EnrollmentClassLevel.CP2,
            gender=Gender.MALE,
            count=80,
        )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = TransitionRateService(db_session)
    result = await svc.compute_transitions(
        [trans_ctx["year_from"].id], admin,
    )

    # 8 paires × 2 genres × (2 régions + 1 NATIONAL) = 48 rows.
    expected_rows = len(LEVEL_PAIRS) * 2 * 3
    assert result.computed == expected_rows
    rows = (await db_session.execute(
        select(TransitionRate).where(
            TransitionRate.schoolYearFromId == trans_ctx["year_from"].id,
        )
    )).scalars().all()
    assert len(rows) == expected_rows

    # Vérif CP1→CP2 FEMALE pour région A.
    cp1_cp2_female_a = next(
        r for r in rows
        if r.classLevelFrom == EnrollmentClassLevel.CP1
        and r.classLevelTo == EnrollmentClassLevel.CP2
        and r.gender == Gender.FEMALE
        and r.scope == TransitionScope.REGIONAL
        and r.entityId == trans_ctx["region_a"].id
    )
    assert cp1_cp2_female_a.rate == Decimal("0.8000")
    assert cp1_cp2_female_a.sampleSize == 100
    assert cp1_cp2_female_a.isOutlier is False


# ===========================================================================
# 5. Skip silencieux si pas de SchoolYear successeur
# ===========================================================================
async def test_compute_transitions_skips_when_no_consecutive_school_year(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    """year_to (la plus récente) n'a pas de successeur → skip."""
    # Ne PAS créer year_to+1 — la year_to est la dernière connue.
    admin = await _make_admin_user(db_session)
    svc = TransitionRateService(db_session)
    result = await svc.compute_transitions(
        [trans_ctx["year_to"].id], admin,
    )
    assert result.computed == 0
    assert result.outliers == 0
    assert result.skipped == [trans_ctx["year_to"].id]


# ===========================================================================
# 6. compute_transitions refusé hors NATIONAL/MINISTRY
# ===========================================================================
async def test_compute_transitions_requires_admin(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    teacher = await _make_admin_user(db_session, UserRole.TEACHER)
    svc = TransitionRateService(db_session)
    with pytest.raises(ForbiddenError):
        await svc.compute_transitions(
            [trans_ctx["year_from"].id], teacher,
        )


# ===========================================================================
# 7. Rate NATIONAL = somme pondérée, pas moyenne simple
# ===========================================================================
async def test_national_rate_is_weighted_average_not_simple_mean(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    """Region A : 100 → 90 (rate 0.9). Region B : 1000 → 500 (rate 0.5).

    Moyenne simple : 0.7. Pondérée : (90+500)/(100+1000) = 590/1100 = 0.5364.
    """
    # Region A : petite cohorte, rate 0.9
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.FEMALE,
        count=100,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.FEMALE,
        count=90,
    )
    # Region B : grosse cohorte, rate 0.5
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_b"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.FEMALE,
        count=1000,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_b"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.FEMALE,
        count=500,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = TransitionRateService(db_session)
    await svc.compute_transitions([trans_ctx["year_from"].id], admin)

    national = (await db_session.execute(
        select(TransitionRate).where(
            TransitionRate.scope == TransitionScope.NATIONAL,
            TransitionRate.classLevelFrom == EnrollmentClassLevel.CP1,
            TransitionRate.gender == Gender.FEMALE,
        )
    )).scalars().one()

    # (90 + 500) / (100 + 1000) = 590 / 1100 = 0.5364 (Decimal 4 dec).
    expected = (Decimal("590") / Decimal("1100")).quantize(Decimal("0.0001"))
    assert national.rate == expected
    assert national.sampleSize == 1100
    # Pondéré != moyenne simple (0.7).
    assert national.rate != Decimal("0.7000")


# ===========================================================================
# 8. Rates désagrégés par genre
# ===========================================================================
async def test_rates_disaggregated_by_gender(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CE1,
        gender=Gender.FEMALE,
        count=50,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CE2,
        gender=Gender.FEMALE,
        count=40,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CE1,
        gender=Gender.MALE,
        count=50,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CE2,
        gender=Gender.MALE,
        count=45,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = TransitionRateService(db_session)
    await svc.compute_transitions([trans_ctx["year_from"].id], admin)

    rows = (await db_session.execute(
        select(TransitionRate).where(
            TransitionRate.scope == TransitionScope.REGIONAL,
            TransitionRate.entityId == trans_ctx["region_a"].id,
            TransitionRate.classLevelFrom == EnrollmentClassLevel.CE1,
        )
    )).scalars().all()
    by_gender = {r.gender: r for r in rows}
    assert by_gender[Gender.FEMALE].rate == Decimal("0.8000")
    assert by_gender[Gender.MALE].rate == Decimal("0.9000")
    # Les rates sont DIFFÉRENTS — désagrégation effective.
    assert by_gender[Gender.FEMALE].rate != by_gender[Gender.MALE].rate


# ===========================================================================
# 9. Rates désagrégés par région
# ===========================================================================
async def test_rates_disaggregated_by_region(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    # Region A : rate 0.6. Region B : rate 0.9.
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.MALE,
        count=100,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.MALE,
        count=60,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_b"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.MALE,
        count=100,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_b"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.MALE,
        count=90,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = TransitionRateService(db_session)
    await svc.compute_transitions([trans_ctx["year_from"].id], admin)

    rate_a = (await db_session.execute(
        select(TransitionRate).where(
            TransitionRate.scope == TransitionScope.REGIONAL,
            TransitionRate.entityId == trans_ctx["region_a"].id,
            TransitionRate.classLevelFrom == EnrollmentClassLevel.CP1,
            TransitionRate.gender == Gender.MALE,
        )
    )).scalars().one()
    rate_b = (await db_session.execute(
        select(TransitionRate).where(
            TransitionRate.scope == TransitionScope.REGIONAL,
            TransitionRate.entityId == trans_ctx["region_b"].id,
            TransitionRate.classLevelFrom == EnrollmentClassLevel.CP1,
            TransitionRate.gender == Gender.MALE,
        )
    )).scalars().one()
    assert rate_a.rate == Decimal("0.6000")
    assert rate_b.rate == Decimal("0.9000")


# ===========================================================================
# 10. list_rates filtre par région
# ===========================================================================
async def test_list_rates_filters_by_region(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.FEMALE,
        count=50,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.FEMALE,
        count=40,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_b"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.FEMALE,
        count=50,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_b"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.FEMALE,
        count=40,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = TransitionRateService(db_session)
    await svc.compute_transitions([trans_ctx["year_from"].id], admin)

    filters = TransitionRateFilters(
        scope=TransitionScope.REGIONAL,
        entityId=trans_ctx["region_a"].id,
    )
    rates = await svc.list_rates(filters, admin)
    # Tous les rates renvoyés sont scope=REGIONAL et entityId=region_a.
    assert len(rates) > 0
    for r in rates:
        assert r.scope == TransitionScope.REGIONAL
        assert r.entityId == trans_ctx["region_a"].id


# ===========================================================================
# 11. list_rates respecte le scope territorial (REGIONAL_ADMIN)
# ===========================================================================
async def test_list_rates_respects_territorial_scope(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    # Seed pour les 2 régions.
    for sch, region in (
        (trans_ctx["school_a"].id, trans_ctx["region_a"].id),
        (trans_ctx["school_b"].id, trans_ctx["region_b"].id),
    ):
        del region
        _seed_enrollment(
            db_session,
            school_year_id=trans_ctx["year_from"].id,
            school_id=sch,
            class_level=EnrollmentClassLevel.CP1,
            gender=Gender.FEMALE,
            count=50,
        )
        _seed_enrollment(
            db_session,
            school_year_id=trans_ctx["year_to"].id,
            school_id=sch,
            class_level=EnrollmentClassLevel.CP2,
            gender=Gender.FEMALE,
            count=40,
        )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = TransitionRateService(db_session)
    await svc.compute_transitions([trans_ctx["year_from"].id], admin)

    # REGIONAL_ADMIN limité à region_a → ne voit pas region_b.
    reg_admin = await _make_admin_user(
        db_session, UserRole.REGIONAL_ADMIN,
        regionId=trans_ctx["region_a"].id,
    )

    rates = await svc.list_rates(
        TransitionRateFilters(scope=TransitionScope.REGIONAL),
        reg_admin,
    )
    region_ids = {r.entityId for r in rates}
    # Région A est visible, région B NE l'est PAS.
    assert trans_ctx["region_a"].id in region_ids
    assert trans_ctx["region_b"].id not in region_ids


# ===========================================================================
# 12. get_outliers ne renvoie que les rates flaggés
# ===========================================================================
async def test_outliers_endpoint_returns_only_flagged(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    # Region A : rate normal (0.8). Region B : rate aberrant (> 2 → outlier).
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.FEMALE,
        count=100,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.FEMALE,
        count=80,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_b"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.FEMALE,
        count=10,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_b"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.FEMALE,
        count=50,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = TransitionRateService(db_session)
    await svc.compute_transitions([trans_ctx["year_from"].id], admin)

    outliers = await svc.get_outliers(
        admin, school_year_from_id=trans_ctx["year_from"].id,
    )
    # Au moins l'outlier de region B (rate 5.0 > 2).
    assert len(outliers) >= 1
    region_b_outlier = next(
        (o for o in outliers if o.entityId == trans_ctx["region_b"].id),
        None,
    )
    assert region_b_outlier is not None
    assert region_b_outlier.isOutlier is True
    assert region_b_outlier.rate is not None
    assert region_b_outlier.rate > Decimal("2.0")


# ===========================================================================
# 13. Hook Module 9 — anomalies TRANSITION_RATE_OUTLIER créées
# ===========================================================================
async def test_outlier_anomaly_detected(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    # Region A : rate ~ 5.0 (count_from=10, count_to=50) → outlier > 2.
    # Region B : rate ~ 0.3 (count_from=100, count_to=30) → abandon massif < 0.5.
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.FEMALE,
        count=10,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.FEMALE,
        count=50,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_b"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.FEMALE,
        count=100,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_b"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.FEMALE,
        count=30,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = TransitionRateService(db_session)
    result = await svc.compute_transitions(
        [trans_ctx["year_from"].id], admin,
    )
    # On a créé au moins 2 anomalies (region A > 2, region B < 0.5).
    assert result.anomaliesCreated >= 2

    anomalies = (await db_session.execute(
        select(AnomalyDetection).where(
            AnomalyDetection.type == AnomalyType.TRANSITION_RATE_OUTLIER,
        )
    )).scalars().all()
    assert len(anomalies) >= 2
    for a in anomalies:
        assert a.severity.value == "MEDIUM"
        assert a.status == AnomalyStatus.PENDING
        assert a.entityType == "Region"
        # Evidence contient les chiffres source.
        assert a.evidence["thresholdMax"] == 2.0
        assert a.evidence["thresholdMin"] == 0.5
        assert "rate" in a.evidence


# ===========================================================================
# 14. Re-compute idempotent : ré-exécution remplace l'ancien rate
# ===========================================================================
async def test_recompute_same_year_overwrites_old_rate(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.FEMALE,
        count=100,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.FEMALE,
        count=80,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = TransitionRateService(db_session)
    await svc.compute_transitions([trans_ctx["year_from"].id], admin)
    rows_before = (await db_session.execute(
        select(TransitionRate).where(
            TransitionRate.schoolYearFromId == trans_ctx["year_from"].id,
        )
    )).scalars().all()
    count_before = len(rows_before)
    assert count_before > 0

    # Ré-exécute → le nombre de rows reste stable (pas d'accumulation).
    await svc.compute_transitions([trans_ctx["year_from"].id], admin)
    rows_after = (await db_session.execute(
        select(TransitionRate).where(
            TransitionRate.schoolYearFromId == trans_ctx["year_from"].id,
        )
    )).scalars().all()
    assert len(rows_after) == count_before


# ===========================================================================
# 15. compute_transitions renvoie counts computed + outliers
# ===========================================================================
async def test_compute_outputs_count_of_computed_and_outliers(
    db_session: AsyncSession, trans_ctx: dict[str, Any],
) -> None:
    # 1 outlier > 2 sur CP1→CP2 region A FEMALE.
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_from"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP1,
        gender=Gender.FEMALE,
        count=10,
    )
    _seed_enrollment(
        db_session,
        school_year_id=trans_ctx["year_to"].id,
        school_id=trans_ctx["school_a"].id,
        class_level=EnrollmentClassLevel.CP2,
        gender=Gender.FEMALE,
        count=30,  # rate = 3.0 → outlier.
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = TransitionRateService(db_session)
    result = await svc.compute_transitions(
        [trans_ctx["year_from"].id], admin,
    )
    # Seed limité à region_a → 8 paires × 2 genres × (1 region + 1 NAT) = 32.
    assert result.computed == len(LEVEL_PAIRS) * 2 * 2
    # On a au moins 1 outlier (CP1→CP2 FEMALE region A + NATIONAL).
    assert result.outliers >= 1
    assert result.computedAt is not None
    assert result.skipped == []
