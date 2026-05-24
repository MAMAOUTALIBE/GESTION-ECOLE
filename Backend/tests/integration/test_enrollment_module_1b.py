"""Module 1B — Gender Parity Index (GPI) + alertes auto.

Couvre :
1.  compute_gpi (cas basique).
2.  compute_gpi avec boys=0 (sentinelle MALE_ABSENT).
3.  compute_gpi avec girls=boys=0 (None).
4.  classify_gpi (table-driven sur les 4 sévérités).
5.  compute_snapshots persiste à tous les échelons.
6.  get_gpi sur scope SCHOOL retourne la bonne sévérité.
7.  list_critical_schools ne renvoie que les CRITICAL_GIRLS.
8.  gpi_evolution renvoie une série temporelle multi-années.
9.  compute_snapshots refusé hors NATIONAL_ADMIN / MINISTRY_ADMIN.
10. get_gpi met le résultat en cache Redis.
11. compute_snapshots déclenche les anomalies CRITICAL_GPI (hook Module 9).
12. get_national_kpis du cockpit inclut nationalGpi (hook Module 19).
13. Re-compute idempotent : ré-exécution remplace l'ancien snapshot.
14. get_gpi respecte le scope territorial (REGIONAL_ADMIN limité à sa région).
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
from app.modules.anomalies.enums import AnomalyStatus, AnomalyType
from app.modules.anomalies.models import AnomalyDetection
from app.modules.auth.models import User
from app.modules.cockpit.service import CockpitService
from app.modules.enrollment.enums import (
    EnrollmentClassLevel,
    EnrollmentSource,
    GpiScope,
)
from app.modules.enrollment.models import Enrollment, GpiSnapshot
from app.modules.enrollment.parity import (
    GPI_THRESHOLDS,
    MALE_ABSENT_GPI,
    GpiSeverity,
    classify_gpi,
    compute_gpi,
)
from app.modules.enrollment.service import EnrollmentService
from app.shared.base import generate_cuid
from app.shared.enums import AcademicPeriodType, Gender, UserRole
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
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
    session: AsyncSession, role: UserRole = UserRole.NATIONAL_ADMIN,
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


@pytest_asyncio.fixture(loop_scope="session")
async def gpi_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Setup : un arbre territorial + 3 écoles + 1 SchoolYear active."""
    factories.bind(db_session)
    region = await factories.RegionFactory.create_async()
    prefecture = await factories.PrefectureFactory.create_async(
        regionId=region.id
    )
    sub = await factories.SubPrefectureFactory.create_async(
        regionId=region.id, prefectureId=prefecture.id,
    )
    schools = []
    for _ in range(3):
        s = await factories.SchoolFactory.create_async(
            regionId=region.id,
            prefectureId=prefecture.id,
            subPrefectureId=sub.id,
        )
        schools.append(s)

    year = await _make_school_year(db_session)
    return {
        "region": region,
        "prefecture": prefecture,
        "subPrefecture": sub,
        "schools": schools,
        "year": year,
    }


def _seed_enrollment(
    session: AsyncSession,
    *,
    school_year_id: str,
    school_id: str,
    girls: int,
    boys: int,
    class_level: EnrollmentClassLevel = EnrollmentClassLevel.CP1,
) -> None:
    """Ajoute 2 rows (FEMALE + MALE) pour une école/année.

    Utilise des class_levels variables pour éviter les collisions sur la
    contrainte d'unicité (year × school × level × gender × source).
    """
    now = datetime.now(UTC)
    session.add(Enrollment(
        schoolYearId=school_year_id,
        schoolId=school_id,
        classLevel=class_level,
        gender=Gender.FEMALE,
        count=girls,
        source=EnrollmentSource.CENSUS_DECLARED,
        recordedAt=now,
    ))
    session.add(Enrollment(
        schoolYearId=school_year_id,
        schoolId=school_id,
        classLevel=class_level,
        gender=Gender.MALE,
        count=boys,
        source=EnrollmentSource.CENSUS_DECLARED,
        recordedAt=now,
    ))


# ===========================================================================
# 1. compute_gpi basique
# ===========================================================================
def test_compute_gpi_basic() -> None:
    """40 filles / 50 garçons = 0.8000 (4 décimales, Decimal)."""
    result = compute_gpi(40, 50)
    assert result is not None
    assert isinstance(result, Decimal)
    assert result == Decimal("0.8000")

    # Cas parité parfaite.
    assert compute_gpi(50, 50) == Decimal("1.0000")
    # Cas léger déséquilibre.
    assert compute_gpi(48, 50) == Decimal("0.9600")


# ===========================================================================
# 2. compute_gpi boys=0 → MALE_ABSENT symbolique
# ===========================================================================
def test_compute_gpi_boys_zero_returns_symbolic_value() -> None:
    """Cohorte 100% filles : retourne la sentinelle Decimal(999.9999)."""
    result = compute_gpi(30, 0)
    assert result == MALE_ABSENT_GPI
    assert result == Decimal("999.9999")
    # Validation seuils export.
    assert GPI_THRESHOLDS["MALE_ABSENT_GPI"] == MALE_ABSENT_GPI


# ===========================================================================
# 3. compute_gpi girls=boys=0 → None
# ===========================================================================
def test_compute_gpi_both_zero_returns_none() -> None:
    """Cohorte vide : rien à mesurer."""
    assert compute_gpi(0, 0) is None
    # classify(None) = NORMAL (pas d'alerte sur l'absence de data).
    assert classify_gpi(None) == GpiSeverity.NORMAL


# ===========================================================================
# 4. classify_gpi (table-driven sur les 4 sévérités)
# ===========================================================================
@pytest.mark.parametrize(
    "gpi,expected",
    [
        (Decimal("0.5"), GpiSeverity.CRITICAL_GIRLS),    # < 0.85
        (Decimal("0.84"), GpiSeverity.CRITICAL_GIRLS),
        (Decimal("0.85"), GpiSeverity.WARNING_GIRLS),    # 0.85 .. 0.97
        (Decimal("0.9"), GpiSeverity.WARNING_GIRLS),
        (Decimal("0.97"), GpiSeverity.NORMAL),           # 0.97 .. 1.03
        (Decimal("1.0"), GpiSeverity.NORMAL),
        (Decimal("1.03"), GpiSeverity.NORMAL),
        (Decimal("1.1"), GpiSeverity.WARNING_BOYS),      # > 1.03
        (Decimal("2.0"), GpiSeverity.WARNING_BOYS),
        (MALE_ABSENT_GPI, GpiSeverity.CRITICAL_GIRLS),   # sentinelle
    ],
)
def test_classify_gpi_thresholds(gpi: Decimal, expected: GpiSeverity) -> None:
    assert classify_gpi(gpi) == expected


# ===========================================================================
# 5. compute_snapshots persiste à tous les échelons
# ===========================================================================
async def test_compute_snapshots_persists_all_scopes(
    db_session: AsyncSession, gpi_ctx: dict[str, Any],
) -> None:
    # 3 écoles dans 1 préfecture, 1 région.
    for sch in gpi_ctx["schools"]:
        _seed_enrollment(
            db_session,
            school_year_id=gpi_ctx["year"].id,
            school_id=sch.id,
            girls=20, boys=25,
        )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = EnrollmentService(db_session)
    result = await svc.compute_gpi_snapshots(gpi_ctx["year"].id, admin)

    # On doit avoir 3 SCHOOL + 1 PREFECTURE + 1 REGIONAL + 1 NATIONAL.
    assert result.persisted[GpiScope.SCHOOL.value] == 3
    assert result.persisted[GpiScope.PREFECTURE.value] == 1
    assert result.persisted[GpiScope.REGIONAL.value] == 1
    assert result.persisted[GpiScope.NATIONAL.value] == 1

    # Vérif en DB côté SCHOOL.
    rows = (await db_session.execute(
        select(GpiSnapshot).where(
            GpiSnapshot.scope == GpiScope.SCHOOL,
            GpiSnapshot.schoolYearId == gpi_ctx["year"].id,
        )
    )).scalars().all()
    assert len(rows) == 3
    # Tous les GPI doivent valoir 20/25 = 0.8 → CRITICAL_GIRLS.
    for r in rows:
        assert r.gpi == Decimal("0.8000")
        assert r.severity == GpiSeverity.CRITICAL_GIRLS

    # NATIONAL rollup : 60/75 = 0.8 (3 écoles cumulées).
    national = (await db_session.execute(
        select(GpiSnapshot).where(
            GpiSnapshot.scope == GpiScope.NATIONAL,
            GpiSnapshot.schoolYearId == gpi_ctx["year"].id,
        )
    )).scalars().one()
    assert national.gpi == Decimal("0.8000")
    assert national.entityId is None  # invariant scope=NATIONAL
    assert national.girlsCount == 60
    assert national.boysCount == 75


# ===========================================================================
# 6. get_gpi sur une école retourne la bonne sévérité
# ===========================================================================
async def test_get_gpi_school_returns_correct_severity(
    db_session: AsyncSession, gpi_ctx: dict[str, Any],
) -> None:
    # École 1 : NORMAL (50/50 → 1.0)
    _seed_enrollment(
        db_session,
        school_year_id=gpi_ctx["year"].id,
        school_id=gpi_ctx["schools"][0].id,
        girls=50, boys=50,
    )
    # École 2 : CRITICAL (15/50 = 0.3 → CRITICAL_GIRLS)
    _seed_enrollment(
        db_session,
        school_year_id=gpi_ctx["year"].id,
        school_id=gpi_ctx["schools"][1].id,
        girls=15, boys=50,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = EnrollmentService(db_session)
    await svc.compute_gpi_snapshots(gpi_ctx["year"].id, admin)

    # École 1 → NORMAL.
    r1 = await svc.get_gpi(
        GpiScope.SCHOOL, admin,
        entity_id=gpi_ctx["schools"][0].id,
        school_year_id=gpi_ctx["year"].id,
    )
    assert r1.severity == GpiSeverity.NORMAL
    assert r1.gpi == Decimal("1.0000")

    # École 2 → CRITICAL_GIRLS.
    r2 = await svc.get_gpi(
        GpiScope.SCHOOL, admin,
        entity_id=gpi_ctx["schools"][1].id,
        school_year_id=gpi_ctx["year"].id,
    )
    assert r2.severity == GpiSeverity.CRITICAL_GIRLS
    assert r2.gpi == Decimal("0.3000")


# ===========================================================================
# 7. list_critical_schools ne renvoie que les CRITICAL_GIRLS
# ===========================================================================
async def test_list_critical_schools_returns_only_critical(
    db_session: AsyncSession, gpi_ctx: dict[str, Any],
) -> None:
    # École 0 → NORMAL (50/50), école 1 → WARNING (45/50), école 2 → CRITICAL (10/50).
    seeds = [(50, 50), (45, 50), (10, 50)]
    for sch, (g, b) in zip(gpi_ctx["schools"], seeds, strict=False):
        _seed_enrollment(
            db_session,
            school_year_id=gpi_ctx["year"].id,
            school_id=sch.id,
            girls=g, boys=b,
        )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = EnrollmentService(db_session)
    await svc.compute_gpi_snapshots(gpi_ctx["year"].id, admin)

    critical = await svc.list_critical_schools(
        gpi_ctx["year"].id, admin, limit=10,
    )
    assert len(critical) == 1
    assert critical[0].entityId == gpi_ctx["schools"][2].id
    assert critical[0].severity == GpiSeverity.CRITICAL_GIRLS


# ===========================================================================
# 8. gpi_evolution renvoie une série multi-années
# ===========================================================================
async def test_gpi_evolution_returns_temporal_series(
    db_session: AsyncSession, gpi_ctx: dict[str, Any],
) -> None:
    # 2e année (2024).
    year_prev = await _make_school_year(
        db_session, year=2024, is_active=False, name="YEAR-PREV",
    )

    school = gpi_ctx["schools"][0]
    # Année courante : 30/30 → 1.0 NORMAL.
    _seed_enrollment(
        db_session,
        school_year_id=gpi_ctx["year"].id,
        school_id=school.id,
        girls=30, boys=30,
    )
    # Année précédente : 20/30 → 0.667 CRITICAL.
    _seed_enrollment(
        db_session,
        school_year_id=year_prev.id,
        school_id=school.id,
        girls=20, boys=30,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = EnrollmentService(db_session)
    await svc.compute_gpi_snapshots(gpi_ctx["year"].id, admin)
    await svc.compute_gpi_snapshots(year_prev.id, admin)

    series = await svc.gpi_evolution(
        GpiScope.SCHOOL, school.id,
        [year_prev.id, gpi_ctx["year"].id],
        admin,
    )
    assert len(series) == 2
    # Tri chronologique par computedAt — l'année prev devrait sortir en
    # premier (snapshot calculé après l'autre, mais le service trie par
    # computedAt ASC : on garde donc l'ordre d'écriture).
    found_years = {p.schoolYearId for p in series}
    assert found_years == {gpi_ctx["year"].id, year_prev.id}
    # Vérif sévérités.
    by_year = {p.schoolYearId: p for p in series}
    assert by_year[gpi_ctx["year"].id].severity == GpiSeverity.NORMAL
    assert by_year[year_prev.id].severity == GpiSeverity.CRITICAL_GIRLS


# ===========================================================================
# 9. compute_snapshots refusé hors admin central
# ===========================================================================
async def test_compute_snapshots_requires_admin(
    db_session: AsyncSession, gpi_ctx: dict[str, Any],
) -> None:
    reg_admin = await _make_admin_user(
        db_session, UserRole.REGIONAL_ADMIN,
        regionId=gpi_ctx["region"].id,
    )
    svc = EnrollmentService(db_session)
    with pytest.raises(ForbiddenError):
        await svc.compute_gpi_snapshots(gpi_ctx["year"].id, reg_admin)


# ===========================================================================
# 10. get_gpi : résultat mis en cache Redis (2e appel sert le cache)
# ===========================================================================
async def test_gpi_snapshot_cached_in_redis(
    db_session: AsyncSession, gpi_ctx: dict[str, Any], redis_client,
) -> None:
    school = gpi_ctx["schools"][0]
    _seed_enrollment(
        db_session,
        school_year_id=gpi_ctx["year"].id,
        school_id=school.id,
        girls=40, boys=40,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = EnrollmentService(db_session)
    await svc.compute_gpi_snapshots(gpi_ctx["year"].id, admin)

    # 1er appel → écrit dans le cache.
    r1 = await svc.get_gpi(
        GpiScope.SCHOOL, admin,
        entity_id=school.id,
        school_year_id=gpi_ctx["year"].id,
    )
    assert r1.gpi == Decimal("1.0000")

    # Vérif présence de la clé dans Redis.
    expected_key = (
        f"enrollment:gpi:SCHOOL:{school.id}:{gpi_ctx['year'].id}"
    )
    cached = await redis_client.get(expected_key)
    assert cached is not None, (
        f"Cache key {expected_key} introuvable dans Redis test DB"
    )

    # 2e appel — le code passe par le cache (idempotent métier, valeur identique).
    r2 = await svc.get_gpi(
        GpiScope.SCHOOL, admin,
        entity_id=school.id,
        school_year_id=gpi_ctx["year"].id,
    )
    assert r2.gpi == r1.gpi
    assert r2.severity == r1.severity


# ===========================================================================
# 11. Hook Module 9 — compute_snapshots crée les anomalies CRITICAL_GPI
# ===========================================================================
async def test_critical_gpi_creates_anomaly_record(
    db_session: AsyncSession, gpi_ctx: dict[str, Any],
) -> None:
    # 2 écoles CRITICAL + 1 normale.
    seeds = [(10, 50), (12, 50), (50, 50)]
    for sch, (g, b) in zip(gpi_ctx["schools"], seeds, strict=False):
        _seed_enrollment(
            db_session,
            school_year_id=gpi_ctx["year"].id,
            school_id=sch.id,
            girls=g, boys=b,
        )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = EnrollmentService(db_session)
    result = await svc.compute_gpi_snapshots(gpi_ctx["year"].id, admin)
    assert result.criticalAnomaliesCreated == 2

    # Vérif côté table AnomalyDetection.
    anomalies = (await db_session.execute(
        select(AnomalyDetection).where(
            AnomalyDetection.type == AnomalyType.CRITICAL_GPI
        )
    )).scalars().all()
    assert len(anomalies) == 2
    for a in anomalies:
        assert a.severity.value == "HIGH"
        assert a.status == AnomalyStatus.PENDING
        assert a.entityType == "School"
        assert a.entityId in {
            gpi_ctx["schools"][0].id, gpi_ctx["schools"][1].id,
        }
        # L'evidence contient les chiffres source.
        assert a.evidence["thresholdMax"] == 0.85
        assert a.evidence["girlsCount"] in (10, 12)
        assert a.evidence["boysCount"] == 50


# ===========================================================================
# 12. Hook Module 19 — get_national_kpis inclut nationalGpi
# ===========================================================================
async def test_cockpit_kpis_includes_national_gpi(
    db_session: AsyncSession, gpi_ctx: dict[str, Any], redis_client,
) -> None:
    # Setup data + snapshots.
    for sch in gpi_ctx["schools"]:
        _seed_enrollment(
            db_session,
            school_year_id=gpi_ctx["year"].id,
            school_id=sch.id,
            girls=40, boys=50,  # 40/50 = 0.8 → CRITICAL
        )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    enroll_svc = EnrollmentService(db_session)
    await enroll_svc.compute_gpi_snapshots(gpi_ctx["year"].id, admin)

    # Force cache cockpit vide pour récupérer la valeur fraîche.
    await redis_client.delete("cockpit:kpis:national")

    cockpit = CockpitService(db_session)
    kpis = await cockpit.get_national_kpis()
    assert kpis.nationalGpi is not None
    assert kpis.nationalGpi == Decimal("0.8000")
    # Le mapping ``items`` contient aussi la clé.
    assert "NATIONAL_GPI" in kpis.items
    assert kpis.items["NATIONAL_GPI"] == pytest.approx(0.8, rel=1e-3)


# ===========================================================================
# 13. Re-compute idempotent — l'ancien snapshot est remplacé
# ===========================================================================
async def test_recompute_idempotent_overwrites_old_snapshot(
    db_session: AsyncSession, gpi_ctx: dict[str, Any],
) -> None:
    school = gpi_ctx["schools"][0]
    # Première saisie : 50/50 → NORMAL.
    _seed_enrollment(
        db_session,
        school_year_id=gpi_ctx["year"].id,
        school_id=school.id,
        girls=50, boys=50,
    )
    await db_session.flush()
    admin = await _make_admin_user(db_session)
    svc = EnrollmentService(db_session)
    await svc.compute_gpi_snapshots(gpi_ctx["year"].id, admin)

    snaps_before = (await db_session.execute(
        select(GpiSnapshot).where(
            GpiSnapshot.schoolYearId == gpi_ctx["year"].id,
        )
    )).scalars().all()
    count_before = len(snaps_before)
    assert count_before > 0

    # On modifie les effectifs (nouvelle saisie sur un AUTRE niveau pour
    # ne pas violer la contrainte unique).
    _seed_enrollment(
        db_session,
        school_year_id=gpi_ctx["year"].id,
        school_id=school.id,
        girls=10, boys=50,
        class_level=EnrollmentClassLevel.CE1,
    )
    await db_session.flush()
    await svc.compute_gpi_snapshots(gpi_ctx["year"].id, admin)

    # Le nombre total de snapshots est resté STABLE (pas d'accumulation).
    snaps_after = (await db_session.execute(
        select(GpiSnapshot).where(
            GpiSnapshot.schoolYearId == gpi_ctx["year"].id,
        )
    )).scalars().all()
    assert len(snaps_after) == count_before

    # Le snapshot de l'école reflète maintenant le nouveau total
    # (60 filles / 100 garçons = 0.6 → CRITICAL).
    school_snap = (await db_session.execute(
        select(GpiSnapshot).where(
            GpiSnapshot.scope == GpiScope.SCHOOL,
            GpiSnapshot.entityId == school.id,
            GpiSnapshot.schoolYearId == gpi_ctx["year"].id,
        )
    )).scalars().one()
    assert school_snap.girlsCount == 60
    assert school_snap.boysCount == 100
    assert school_snap.severity == GpiSeverity.CRITICAL_GIRLS


# ===========================================================================
# 14. RBAC — REGIONAL_ADMIN limité à sa région
# ===========================================================================
async def test_get_gpi_respects_territorial_scope(
    db_session: AsyncSession, gpi_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    # Crée une AUTRE région avec une école dedans.
    other_region = await factories.RegionFactory.create_async()
    other_school = await factories.SchoolFactory.create_async(
        regionId=other_region.id,
    )

    # Seed les 2 écoles (la 1ʳᵉ région + l'autre).
    _seed_enrollment(
        db_session,
        school_year_id=gpi_ctx["year"].id,
        school_id=gpi_ctx["schools"][0].id,
        girls=40, boys=40,
    )
    _seed_enrollment(
        db_session,
        school_year_id=gpi_ctx["year"].id,
        school_id=other_school.id,
        girls=40, boys=40,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = EnrollmentService(db_session)
    await svc.compute_gpi_snapshots(gpi_ctx["year"].id, admin)

    reg_admin = await _make_admin_user(
        db_session, UserRole.REGIONAL_ADMIN,
        regionId=gpi_ctx["region"].id,
    )

    # Le REGIONAL_ADMIN peut lire SA région.
    r = await svc.get_gpi(
        GpiScope.REGIONAL, reg_admin,
        entity_id=gpi_ctx["region"].id,
        school_year_id=gpi_ctx["year"].id,
    )
    assert r.entityId == gpi_ctx["region"].id

    # Il ne peut PAS lire celle d'une autre région.
    with pytest.raises(ForbiddenError):
        await svc.get_gpi(
            GpiScope.REGIONAL, reg_admin,
            entity_id=other_region.id,
            school_year_id=gpi_ctx["year"].id,
        )

    # list_critical_schools : un REGIONAL_ADMIN ne voit que sa région.
    # On force chacune des écoles à CRITICAL pour rendre la check
    # significative.
    # (les seeds ci-dessus sont 40/40 → NORMAL, donc on ré-écrit).
    await db_session.execute(
        select(Enrollment)  # no-op pour rester côté async
    )

    # On efface puis re-seed avec données critiques.
    await db_session.execute(
        # Delete les rows pré-existantes pour ces écoles.
        Enrollment.__table__.delete().where(
            Enrollment.schoolYearId == gpi_ctx["year"].id,
        )
    )
    _seed_enrollment(
        db_session,
        school_year_id=gpi_ctx["year"].id,
        school_id=gpi_ctx["schools"][0].id,
        girls=10, boys=50,
    )
    _seed_enrollment(
        db_session,
        school_year_id=gpi_ctx["year"].id,
        school_id=other_school.id,
        girls=10, boys=50,
    )
    await db_session.flush()
    await svc.compute_gpi_snapshots(gpi_ctx["year"].id, admin)

    listed = await svc.list_critical_schools(
        gpi_ctx["year"].id, reg_admin, limit=10,
    )
    # Le REGIONAL_ADMIN ne voit que SES écoles (1) — pas l'école d'une
    # autre région, même si elle est aussi CRITICAL.
    assert len(listed) == 1
    assert listed[0].entityId == gpi_ctx["schools"][0].id
