"""Module 1C — Segmentation urbain / rural / péri-urbain.

Couvre :
 1. Default zone RURAL après migration (sub-préf nouvellement créée).
 2. set_sub_prefecture_zone_type persiste + AuditLog.
 3. set_school_zone_type_override persiste + AuditLog.
 4. effective_zone_type : override école gagne sur défaut sous-préf.
 5. effective_zone_type : fallback sur sous-préf si pas d'override.
 6. set_zone_type requires admin role (TEACHER 403, NATIONAL 200).
 7. Enrollment aggregate by_zone_type renvoie le breakdown 3 zones.
 8. Cockpit urban_rural_gap KPI calculé (urban vs rural avec delta).
 9. detect_urban_rural_gpi_gap : anomalie HIGH au-dessus du seuil.
10. clear_school_zone_type_override : retour à la zone héritée.
11. list_sub_prefectures_with_zone : compteurs écoles par zone.
12. Valeur ZoneType inconnue rejetée 422.
13. set_sub_prefecture_zone_type invalide le cache cockpit.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError
from app.modules.academics.models import SchoolYear
from app.modules.anomalies.detectors import detect_urban_rural_gpi_gap
from app.modules.anomalies.enums import AnomalySeverity, AnomalyType
from app.modules.auth.models import AuthAuditLog, User
from app.modules.cockpit.service import CockpitService
from app.modules.enrollment.enums import (
    EnrollmentClassLevel,
    EnrollmentSource,
)
from app.modules.enrollment.models import Enrollment
from app.modules.enrollment.schemas import (
    AggregateRequest,
    AggregateScope,
)
from app.modules.enrollment.service import EnrollmentService
from app.modules.schools.models import School
from app.modules.schools.service import SchoolsService
from app.modules.territory.models import SubPrefecture
from app.modules.territory.service import TerritoryService
from app.modules.territory.zone_type import (
    effective_zone_type,
    get_effective_zone_for_school_id,
)
from app.shared.base import generate_cuid
from app.shared.enums import AcademicPeriodType, Gender, UserRole, ZoneType
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
    girls: int,
    boys: int,
    class_level: EnrollmentClassLevel = EnrollmentClassLevel.CP1,
) -> None:
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


@pytest_asyncio.fixture(loop_scope="session")
async def zone_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Setup : 1 région, 1 préfecture, 2 sous-préfectures (1 urbaine + 1 rurale),
    chacune avec 1 école.
    """
    factories.bind(db_session)
    region = await factories.RegionFactory.create_async()
    prefecture = await factories.PrefectureFactory.create_async(
        regionId=region.id,
    )
    # Sous-préfecture URBAINE (override via ORM).
    sub_urban = await factories.SubPrefectureFactory.create_async(
        regionId=region.id, prefectureId=prefecture.id,
    )
    sub_urban.defaultZoneType = ZoneType.URBAN
    # Sous-préfecture RURALE (valeur par défaut, on garde).
    sub_rural = await factories.SubPrefectureFactory.create_async(
        regionId=region.id, prefectureId=prefecture.id,
    )
    school_urban = await factories.SchoolFactory.create_async(
        regionId=region.id,
        prefectureId=prefecture.id,
        subPrefectureId=sub_urban.id,
    )
    school_rural = await factories.SchoolFactory.create_async(
        regionId=region.id,
        prefectureId=prefecture.id,
        subPrefectureId=sub_rural.id,
    )
    year = await _make_school_year(db_session)
    await db_session.flush()
    return {
        "region": region,
        "prefecture": prefecture,
        "subUrban": sub_urban,
        "subRural": sub_rural,
        "schoolUrban": school_urban,
        "schoolRural": school_rural,
        "year": year,
    }


# ===========================================================================
# 1. Default zone RURAL après migration
# ===========================================================================
async def test_default_zone_type_is_rural_after_migration(
    db_session: AsyncSession,
) -> None:
    """Une sous-préfecture créée sans valeur explicite hérite de RURAL."""
    factories.bind(db_session)
    region = await factories.RegionFactory.create_async()
    prefecture = await factories.PrefectureFactory.create_async(
        regionId=region.id,
    )
    sub = await factories.SubPrefectureFactory.create_async(
        regionId=region.id, prefectureId=prefecture.id,
    )
    # Recharge depuis la DB pour vérifier le default serveur.
    fresh = await db_session.get(SubPrefecture, sub.id)
    assert fresh is not None
    assert fresh.defaultZoneType == ZoneType.RURAL


# ===========================================================================
# 2. set_sub_prefecture_zone_type persiste + AuditLog
# ===========================================================================
async def test_set_sub_prefecture_zone_type_persists_and_audits(
    db_session: AsyncSession, zone_ctx: dict[str, Any],
) -> None:
    admin = await _make_admin_user(db_session)
    svc = TerritoryService(db_session)
    sub = zone_ctx["subRural"]
    assert sub.defaultZoneType == ZoneType.RURAL

    updated = await svc.set_sub_prefecture_zone_type(
        sub.id, ZoneType.PERI_URBAN, admin,
    )
    assert updated.defaultZoneType == ZoneType.PERI_URBAN

    # Vérifie en DB.
    refreshed = await db_session.get(SubPrefecture, sub.id)
    assert refreshed is not None
    assert refreshed.defaultZoneType == ZoneType.PERI_URBAN

    # AuditLog présent.
    logs = (await db_session.execute(
        select(AuthAuditLog).where(
            AuthAuditLog.event == "SET_SUBPREFECTURE_ZONE_TYPE",
            AuthAuditLog.userId == admin.id,
        )
    )).scalars().all()
    assert len(logs) == 1
    assert "RURAL" in (logs[0].failureReason or "")
    assert "PERI_URBAN" in (logs[0].failureReason or "")
    assert sub.id in (logs[0].failureReason or "")


# ===========================================================================
# 3. set_school_zone_type_override persiste + AuditLog
# ===========================================================================
async def test_set_school_zone_type_override_persists_and_audits(
    db_session: AsyncSession, zone_ctx: dict[str, Any],
) -> None:
    admin = await _make_admin_user(db_session)
    svc = SchoolsService(db_session)
    school = zone_ctx["schoolRural"]
    assert school.zoneType is None

    updated = await svc.set_school_zone_type_override(
        school.id, ZoneType.URBAN, admin,
    )
    assert updated.zoneType == ZoneType.URBAN

    refreshed = await db_session.get(School, school.id)
    assert refreshed is not None
    assert refreshed.zoneType == ZoneType.URBAN

    logs = (await db_session.execute(
        select(AuthAuditLog).where(
            AuthAuditLog.event == "SET_SCHOOL_ZONE_TYPE_OVERRIDE",
            AuthAuditLog.userId == admin.id,
        )
    )).scalars().all()
    assert len(logs) == 1
    msg = logs[0].failureReason or ""
    assert school.id in msg
    assert "INHERIT" in msg
    assert "URBAN" in msg


# ===========================================================================
# 4. effective_zone_type : override école gagne
# ===========================================================================
async def test_effective_zone_type_uses_override_when_set(
    db_session: AsyncSession, zone_ctx: dict[str, Any],
) -> None:
    school = zone_ctx["schoolRural"]
    sub = zone_ctx["subRural"]
    # Pas d'override → RURAL (hérite).
    assert effective_zone_type(school, sub) == ZoneType.RURAL

    school.zoneType = ZoneType.URBAN
    await db_session.flush()
    # Override gagne.
    assert effective_zone_type(school, sub) == ZoneType.URBAN

    # Helper async indépendant.
    zone = await get_effective_zone_for_school_id(db_session, school.id)
    assert zone == ZoneType.URBAN


# ===========================================================================
# 5. effective_zone_type : fallback sur défaut sous-préf
# ===========================================================================
async def test_effective_zone_type_falls_back_to_sub_prefecture_default(
    db_session: AsyncSession, zone_ctx: dict[str, Any],
) -> None:
    school = zone_ctx["schoolUrban"]
    sub = zone_ctx["subUrban"]
    assert school.zoneType is None
    assert sub.defaultZoneType == ZoneType.URBAN
    # Pas d'override → hérite URBAN.
    assert effective_zone_type(school, sub) == ZoneType.URBAN

    zone = await get_effective_zone_for_school_id(db_session, school.id)
    assert zone == ZoneType.URBAN


# ===========================================================================
# 6. set_zone_type requires admin role
# ===========================================================================
async def test_set_zone_type_requires_admin_role(
    db_session: AsyncSession, zone_ctx: dict[str, Any],
) -> None:
    teacher = await _make_admin_user(db_session, role=UserRole.TEACHER)
    territory_svc = TerritoryService(db_session)
    sub = zone_ctx["subRural"]

    with pytest.raises(ForbiddenError):
        await territory_svc.set_sub_prefecture_zone_type(
            sub.id, ZoneType.URBAN, teacher,
        )

    # NATIONAL_ADMIN → OK.
    admin = await _make_admin_user(db_session, role=UserRole.NATIONAL_ADMIN)
    result = await territory_svc.set_sub_prefecture_zone_type(
        sub.id, ZoneType.URBAN, admin,
    )
    assert result.defaultZoneType == ZoneType.URBAN

    # School override : TEACHER refusé aussi.
    school_svc = SchoolsService(db_session)
    school = zone_ctx["schoolRural"]
    with pytest.raises(ForbiddenError):
        await school_svc.set_school_zone_type_override(
            school.id, ZoneType.URBAN, teacher,
        )


# ===========================================================================
# 7. Enrollment aggregate by_zone_type renvoie breakdown
# ===========================================================================
async def test_enrollment_aggregate_by_zone_type_returns_breakdown(
    db_session: AsyncSession, zone_ctx: dict[str, Any],
) -> None:
    # École urbaine : 40F / 50M, École rurale : 20F / 50M.
    _seed_enrollment(
        db_session,
        school_year_id=zone_ctx["year"].id,
        school_id=zone_ctx["schoolUrban"].id,
        girls=40, boys=50,
    )
    _seed_enrollment(
        db_session,
        school_year_id=zone_ctx["year"].id,
        school_id=zone_ctx["schoolRural"].id,
        girls=20, boys=50,
    )
    await db_session.flush()

    admin = await _make_admin_user(db_session)
    svc = EnrollmentService(db_session)
    req = AggregateRequest(
        scope=AggregateScope.NATIONAL,
        schoolYearId=zone_ctx["year"].id,
        byZoneType=True,
    )
    resp = await svc.aggregate(req, admin)

    assert len(resp.byZoneType) == 3  # URBAN, RURAL, PERI_URBAN
    by_zone = {z.zoneType: z for z in resp.byZoneType}

    assert by_zone[ZoneType.URBAN].girlsCount == 40
    assert by_zone[ZoneType.URBAN].boysCount == 50
    assert by_zone[ZoneType.URBAN].total == 90
    assert by_zone[ZoneType.URBAN].gpi == pytest.approx(0.8, rel=1e-3)

    assert by_zone[ZoneType.RURAL].girlsCount == 20
    assert by_zone[ZoneType.RURAL].boysCount == 50
    assert by_zone[ZoneType.RURAL].gpi == pytest.approx(0.4, rel=1e-3)

    assert by_zone[ZoneType.PERI_URBAN].total == 0
    assert by_zone[ZoneType.PERI_URBAN].gpi is None


# ===========================================================================
# 8. Cockpit urban_rural_gap KPI
# ===========================================================================
async def test_cockpit_urban_rural_gap_kpi_computed(
    db_session: AsyncSession, zone_ctx: dict[str, Any], redis_client,
) -> None:
    _seed_enrollment(
        db_session,
        school_year_id=zone_ctx["year"].id,
        school_id=zone_ctx["schoolUrban"].id,
        girls=45, boys=50,  # 0.9 urbain
    )
    _seed_enrollment(
        db_session,
        school_year_id=zone_ctx["year"].id,
        school_id=zone_ctx["schoolRural"].id,
        girls=30, boys=50,  # 0.6 rural
    )
    await db_session.flush()

    cockpit = CockpitService(db_session)
    # Force le cache vide pour récupérer la valeur fraîche.
    await redis_client.delete(
        f"cockpit:urban_rural_gap:{zone_ctx['year'].id}"
    )
    gap = await cockpit.get_urban_rural_gap(zone_ctx["year"].id)

    assert gap.urbanGpi is not None
    assert float(gap.urbanGpi) == pytest.approx(0.9, rel=1e-3)
    assert gap.ruralGpi is not None
    assert float(gap.ruralGpi) == pytest.approx(0.6, rel=1e-3)
    assert gap.deltaGpi is not None
    assert float(gap.deltaGpi) == pytest.approx(0.3, rel=1e-3)
    assert gap.urbanCount == 95
    assert gap.ruralCount == 80


# ===========================================================================
# 9. detect_urban_rural_gpi_gap : anomalie HIGH si écart > seuil
# ===========================================================================
async def test_urban_rural_gpi_gap_anomaly_detected_when_above_threshold(
    db_session: AsyncSession, zone_ctx: dict[str, Any],
) -> None:
    # Écart énorme : urbain 0.9 vs rural 0.4 → delta 0.5 > 0.10.
    _seed_enrollment(
        db_session,
        school_year_id=zone_ctx["year"].id,
        school_id=zone_ctx["schoolUrban"].id,
        girls=45, boys=50,
    )
    _seed_enrollment(
        db_session,
        school_year_id=zone_ctx["year"].id,
        school_id=zone_ctx["schoolRural"].id,
        girls=20, boys=50,
    )
    await db_session.flush()

    anomalies = await detect_urban_rural_gpi_gap(
        db_session, school_year_id=zone_ctx["year"].id,
    )
    assert len(anomalies) == 1
    a = anomalies[0]
    assert a.type == AnomalyType.URBAN_RURAL_GPI_GAP
    assert a.severity == AnomalySeverity.HIGH
    assert a.entityType == "Region"
    assert a.entityId == zone_ctx["region"].id
    assert a.evidence["regionId"] == zone_ctx["region"].id
    assert a.evidence["deltaGpi"] > 0.10
    assert a.evidence["urbanGpi"] == pytest.approx(0.9, rel=1e-3)
    assert a.evidence["ruralGpi"] == pytest.approx(0.4, rel=1e-3)


# ===========================================================================
# 10. clear_school_zone_type_override : retour à la zone héritée
# ===========================================================================
async def test_clear_school_zone_type_override_returns_to_default(
    db_session: AsyncSession, zone_ctx: dict[str, Any],
) -> None:
    admin = await _make_admin_user(db_session)
    svc = SchoolsService(db_session)
    school = zone_ctx["schoolRural"]

    # Pose un override URBAN.
    await svc.set_school_zone_type_override(
        school.id, ZoneType.URBAN, admin,
    )
    refreshed = await db_session.get(School, school.id)
    assert refreshed.zoneType == ZoneType.URBAN

    # Clear : revient à NULL (hérite).
    cleared = await svc.clear_school_zone_type_override(school.id, admin)
    assert cleared.zoneType is None

    again = await db_session.get(School, school.id)
    assert again.zoneType is None

    # La zone effective est désormais celle de la sous-préf (RURAL).
    zone = await get_effective_zone_for_school_id(db_session, school.id)
    assert zone == ZoneType.RURAL


# ===========================================================================
# 11. list_sub_prefectures_with_zone : compteurs écoles
# ===========================================================================
async def test_list_sub_prefectures_with_zone_shows_counts(
    db_session: AsyncSession, zone_ctx: dict[str, Any],
) -> None:
    admin = await _make_admin_user(db_session)
    svc = TerritoryService(db_session)
    rows = await svc.list_sub_prefectures_with_zone(admin)

    by_id = {r.id: r for r in rows}
    urban_row = by_id[zone_ctx["subUrban"].id]
    rural_row = by_id[zone_ctx["subRural"].id]

    # Sub urbaine : 1 école URBAN (hérite).
    assert urban_row.defaultZoneType == ZoneType.URBAN
    assert urban_row.urbanSchoolsCount == 1
    assert urban_row.ruralSchoolsCount == 0
    assert urban_row.totalSchoolsCount == 1

    # Sub rurale : 1 école RURAL (hérite).
    assert rural_row.defaultZoneType == ZoneType.RURAL
    assert rural_row.urbanSchoolsCount == 0
    assert rural_row.ruralSchoolsCount == 1

    # Pose un override sur la school rurale → bascule en URBAN dans les compteurs.
    school_svc = SchoolsService(db_session)
    await school_svc.set_school_zone_type_override(
        zone_ctx["schoolRural"].id, ZoneType.URBAN, admin,
    )
    rows2 = await svc.list_sub_prefectures_with_zone(admin)
    rural_row2 = {r.id: r for r in rows2}[zone_ctx["subRural"].id]
    assert rural_row2.urbanSchoolsCount == 1
    assert rural_row2.ruralSchoolsCount == 0


# ===========================================================================
# 12. ZoneType invalide rejeté
# ===========================================================================
async def test_unknown_zone_type_value_rejected_422(
    client, auth_headers, zone_ctx: dict[str, Any],
) -> None:
    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.put(
        f"/api/territory/sub-prefectures/{zone_ctx['subRural'].id}/zone-type",
        json={"zoneType": "LUNAR_BASE"},
        headers=headers,
    )
    assert resp.status_code == 422


# ===========================================================================
# 13. set_sub_prefecture_zone_type invalide le cache cockpit
# ===========================================================================
async def test_set_sub_prefecture_zone_type_invalidates_cache(
    db_session: AsyncSession,
    zone_ctx: dict[str, Any],
    redis_client,
) -> None:
    # Pose une valeur en cache pour la year courante.
    cache_key = f"cockpit:urban_rural_gap:{zone_ctx['year'].id}"
    await redis_client.set(cache_key, '{"stale": true}', ex=60)
    assert await redis_client.get(cache_key) is not None

    admin = await _make_admin_user(db_session)
    svc = TerritoryService(db_session)
    await svc.set_sub_prefecture_zone_type(
        zone_ctx["subRural"].id, ZoneType.URBAN, admin,
    )

    # Cache vidé.
    assert await redis_client.get(cache_key) is None
