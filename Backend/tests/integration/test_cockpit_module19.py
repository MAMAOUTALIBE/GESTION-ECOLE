"""Module 19 — Cockpit ministériel (KPI live + briefing automatique).

Couvre :
1. KPI nationaux (structure complète + cache Redis 30 s).
2. Top alertes (10 écoles classées descendant).
3. Time series présence (90 jours) et anomalies (12 semaines).
4. Briefing structuré + fallback template.
5. Snapshot quotidien (persistance idempotente).
6. Comparison J/J-1 (variation %).
7. RBAC strict ≥ MINISTRY_ADMIN.
8. Robustesse sur dataset vide.
9. Briefing inclut le top 3 des alertes.
10. Mirror Realtime CRITICAL → cockpit:alert (Module 13 hook).
"""
from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.anomalies.enums import (
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
)
from app.modules.anomalies.models import AnomalyDetection
from app.modules.attendance.models import AttendanceRecord
from app.modules.cockpit.enums import CockpitScope, KpiKey
from app.modules.cockpit.models import CockpitKpiSnapshot
from app.modules.cockpit.service import (
    CACHE_TTL_SECONDS,
    CockpitService,
    _today_utc,
)
from app.shared.base import generate_cuid
from app.shared.enums import (
    AttendanceStatus,
    PersonType,
    UserRole,
)
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# No ANTHROPIC_API_KEY in test session — briefing tests run in fallback mode
# by default, and the LLM test sets the env var explicitly for one call.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_anthropic_key_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Seeds : un petit jeu de données représentatif (1 région, 3 écoles,
# quelques anomalies, attendance, etc.) — suffisant pour valider le format
# des réponses sans charger massivement.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def cockpit_ctx(db_session: AsyncSession) -> dict[str, Any]:
    factories.bind(db_session)
    region = await factories.RegionFactory.create_async()
    prefecture = await factories.PrefectureFactory.create_async(regionId=region.id)
    sub = await factories.SubPrefectureFactory.create_async(
        regionId=region.id, prefectureId=prefecture.id,
    )

    schools: list[Any] = []
    for _ in range(3):
        s = await factories.SchoolFactory.create_async(
            regionId=region.id,
            prefectureId=prefecture.id,
            subPrefectureId=sub.id,
        )
        schools.append(s)

    # 10 students total
    for s in schools:
        await factories.StudentFactory.create_batch_async(3, schoolId=s.id)

    return {
        "region": region,
        "prefecture": prefecture,
        "subPrefecture": sub,
        "schools": schools,
    }


async def _seed_anomalies(
    db_session: AsyncSession,
    ctx: dict[str, Any],
    *,
    pending_critical: int = 2,
    pending_medium: int = 4,
    pending_per_school: dict[str, int] | None = None,
) -> None:
    now = datetime.now(UTC)
    region_id = ctx["region"].id
    schools = ctx["schools"]
    pending_per_school = pending_per_school or {}

    # 2 CRITICAL pending sur la 1ʳᵉ école
    for _ in range(pending_critical):
        db_session.add(AnomalyDetection(
            id=generate_cuid(),
            type=AnomalyType.IMPOSSIBLE_GRADE,
            severity=AnomalySeverity.CRITICAL,
            status=AnomalyStatus.PENDING,
            entityType="Grade",
            entityId=generate_cuid(),
            description="critical",
            evidence={},
            schoolId=schools[0].id,
            regionId=region_id,
            detectedAt=now,
        ))
    # 4 MEDIUM pending réparties sur les 3 écoles (mais surtout school[0])
    for i in range(pending_medium):
        target = schools[0] if i < 3 else schools[1]
        db_session.add(AnomalyDetection(
            id=generate_cuid(),
            type=AnomalyType.SUSPICIOUS_ATTENDANCE,
            severity=AnomalySeverity.MEDIUM,
            status=AnomalyStatus.PENDING,
            entityType="Student",
            entityId=generate_cuid(),
            description="medium",
            evidence={},
            schoolId=target.id,
            regionId=region_id,
            detectedAt=now,
        ))
    # Bonus : ranking explicite per school (utile pour test top alerts)
    for school_id, n in pending_per_school.items():
        for _ in range(n):
            db_session.add(AnomalyDetection(
                id=generate_cuid(),
                type=AnomalyType.SUSPICIOUS_ATTENDANCE,
                severity=AnomalySeverity.LOW,
                status=AnomalyStatus.PENDING,
                entityType="Student",
                entityId=generate_cuid(),
                description="low",
                evidence={},
                schoolId=school_id,
                regionId=region_id,
                detectedAt=now,
            ))
    await db_session.flush()


async def _seed_attendance(
    db_session: AsyncSession,
    ctx: dict[str, Any],
    *,
    days_back: int = 7,
) -> None:
    schools = ctx["schools"]
    # crée 5 records par jour pendant N jours (4 PRESENT + 1 ABSENT)
    today = datetime.now(UTC)
    for d in range(days_back):
        when = today - timedelta(days=d, hours=2)
        for j in range(5):
            status = AttendanceStatus.PRESENT if j < 4 else AttendanceStatus.ABSENT
            db_session.add(AttendanceRecord(
                id=generate_cuid(),
                personType=PersonType.STUDENT,
                status=status,
                scannedAt=when,
                schoolId=schools[j % len(schools)].id,
            ))
    await db_session.flush()


@pytest_asyncio.fixture(loop_scope="session")
async def ministry_headers(auth_headers: Any) -> dict[str, str]:
    return await auth_headers(UserRole.MINISTRY_ADMIN)


@pytest_asyncio.fixture(loop_scope="session")
async def national_headers(auth_headers: Any) -> dict[str, str]:
    return await auth_headers(UserRole.NATIONAL_ADMIN)


@pytest_asyncio.fixture(loop_scope="session")
async def director_headers(
    auth_headers: Any, cockpit_ctx: dict[str, Any],
) -> dict[str, str]:
    return await auth_headers(
        UserRole.SCHOOL_DIRECTOR,
        regionId=cockpit_ctx["region"].id,
        schoolId=cockpit_ctx["schools"][0].id,
    )


# ---------------------------------------------------------------------------
# Helper : on flush le cache cockpit avant chaque test pour éviter qu'un
# test précédent ne biaise la réponse via la valeur cachée.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _flush_cockpit_cache_before_each_test() -> Any:
    from app.core.redis import get_redis

    try:
        r = get_redis()
        keys = await r.keys("cockpit:*")
        if keys:
            await r.delete(*keys)
    except Exception:
        pass
    yield


# ===========================================================================
# 1. KPI nationaux : toutes les clefs sont présentes
# ===========================================================================
@pytest.mark.asyncio
async def test_national_kpis_returns_all_keys(
    db_session: AsyncSession, cockpit_ctx: dict[str, Any],
) -> None:
    await _seed_anomalies(db_session, cockpit_ctx)
    await _seed_attendance(db_session, cockpit_ctx)

    service = CockpitService(db_session)
    response = await service.get_national_kpis()

    # 3 ecoles x 3 etudiants minimum
    assert response.studentsTotal >= 9
    assert 0.0 <= response.attendanceRate <= 100.0
    assert response.criticalAnomaliesOpen >= 2
    assert response.alertsOpen >= 6  # 2 critical + 4 medium
    # items contient bien les 5 clés normalisées
    keys = set(response.items.keys())
    assert keys == {k.value for k in KpiKey}
    assert response.cached is False


# ===========================================================================
# 2. Cache Redis 30 s
# ===========================================================================
@pytest.mark.asyncio
async def test_national_kpis_caches_in_redis_30s(
    db_session: AsyncSession, cockpit_ctx: dict[str, Any],
) -> None:
    from app.core.redis import get_redis

    service = CockpitService(db_session)
    first = await service.get_national_kpis()
    assert first.cached is False

    second = await service.get_national_kpis()
    assert second.cached is True

    # Vérifie le TTL exact ≤ 30 (et > 0) côté Redis
    redis = get_redis()
    ttl = await redis.ttl("cockpit:kpis:national")
    assert 0 < ttl <= CACHE_TTL_SECONDS


# ===========================================================================
# 3. Top alertes : 10 écoles classées par anomalies count desc
# ===========================================================================
@pytest.mark.asyncio
async def test_top_alerts_returns_10_schools_ranked(
    db_session: AsyncSession, cockpit_ctx: dict[str, Any],
) -> None:
    # On crée 12 écoles supplémentaires avec des comptes décroissants.
    factories.bind(db_session)
    region_id = cockpit_ctx["region"].id
    extras = []
    for _ in range(12):
        s = await factories.SchoolFactory.create_async(regionId=region_id)
        extras.append(s)
    # Ranking voulu : extras[0] = 12 anomalies, extras[1] = 11, ..., extras[11] = 1
    counts: dict[str, int] = {}
    for i, s in enumerate(extras):
        n = 12 - i
        counts[s.id] = n
    await _seed_anomalies(
        db_session, cockpit_ctx,
        pending_critical=0, pending_medium=0,
        pending_per_school=counts,
    )

    service = CockpitService(db_session)
    response = await service.get_top_alerts(limit=10)

    assert len(response.schools) == 10
    # Vérifie que c'est trié desc
    school_counts = [s.anomaliesCount for s in response.schools]
    assert school_counts == sorted(school_counts, reverse=True)
    # Le 1er doit avoir 12 anomalies
    assert response.schools[0].anomaliesCount == 12


# ===========================================================================
# 4. Time series présence — 90 jours, 1 point par jour
# ===========================================================================
@pytest.mark.asyncio
async def test_attendance_timeseries_90_days(
    db_session: AsyncSession, cockpit_ctx: dict[str, Any],
) -> None:
    await _seed_attendance(db_session, cockpit_ctx, days_back=7)
    service = CockpitService(db_session)
    response = await service.get_attendance_timeseries(days=90)
    assert response.granularity == "DAY"
    assert len(response.points) == 90
    # Le dernier point doit dater d'aujourd'hui
    assert response.points[-1].date == _today_utc()
    # Au moins un point avec value > 0 (présence enregistrée)
    assert any(p.value > 0 for p in response.points)


# ===========================================================================
# 5. Time series anomalies — 12 semaines
# ===========================================================================
@pytest.mark.asyncio
async def test_anomaly_timeseries_12_weeks(
    db_session: AsyncSession, cockpit_ctx: dict[str, Any],
) -> None:
    await _seed_anomalies(db_session, cockpit_ctx)
    service = CockpitService(db_session)
    response = await service.get_anomaly_timeseries(weeks=12)
    assert response.granularity == "WEEK"
    assert len(response.points) == 12
    # La somme totale = nb anomalies seedées (test structurel)
    total = sum(p.value for p in response.points)
    assert total >= 6  # 2 critical + 4 medium


# ===========================================================================
# 6. Briefing structuré (format de la réponse)
# ===========================================================================
@pytest.mark.asyncio
async def test_briefing_today_returns_structured_response(
    db_session: AsyncSession, cockpit_ctx: dict[str, Any],
) -> None:
    await _seed_anomalies(db_session, cockpit_ctx)
    await _seed_attendance(db_session, cockpit_ctx)
    service = CockpitService(db_session)
    briefing = await service.generate_briefing()
    assert briefing.headline
    assert len(briefing.bullets) >= 4
    assert briefing.date == _today_utc()
    assert briefing.source in ("llm", "template")
    # Le payload kpis contient bien les clés normalisées
    assert KpiKey.STUDENTS_TOTAL.value in briefing.kpis


# ===========================================================================
# 7. Briefing : fallback template sans ANTHROPIC_API_KEY
# ===========================================================================
@pytest.mark.asyncio
async def test_briefing_uses_template_fallback_without_api_key(
    db_session: AsyncSession,
    cockpit_ctx: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    service = CockpitService(db_session)
    briefing = await service.generate_briefing()
    assert briefing.source == "template"
    # Le brief template inclut toujours le nombre d'élèves dans le headline
    assert "élèves" in briefing.headline or "Brief" in briefing.headline


# ===========================================================================
# 8. Snapshot : persiste les KPI en base
# ===========================================================================
@pytest.mark.asyncio
async def test_snapshot_daily_kpis_persists_rows(
    db_session: AsyncSession, cockpit_ctx: dict[str, Any],
) -> None:
    await _seed_anomalies(db_session, cockpit_ctx)
    service = CockpitService(db_session)
    today = _today_utc()
    result = await service.snapshot_daily_kpis(snapshot_date=today)
    # Module 1B — l'enum KpiKey contient désormais NATIONAL_GPI : on
    # vérifie l'invariant "tous les KPIs sont snapshotés" via la taille
    # de KpiKey plutôt qu'un nombre figé.
    assert result.persisted == len(KpiKey)
    assert set(result.keys) == {k.value for k in KpiKey}

    # Vérifie qu'on a bien 1 row par KpiKey en base pour aujourd'hui
    # (Module 1B ajoute NATIONAL_GPI : on raisonne en fonction de len(KpiKey)).
    rows = (
        await db_session.execute(
            select(CockpitKpiSnapshot).where(
                CockpitKpiSnapshot.snapshotDate == today,
            ),
        )
    ).scalars().all()
    assert len(rows) == len(KpiKey)
    # Tous les rows sont en scope NATIONAL
    assert {r.scope for r in rows} == {CockpitScope.NATIONAL}


# ===========================================================================
# 9. Comparison J/J-1 : variation %
# ===========================================================================
@pytest.mark.asyncio
async def test_comparison_with_yesterday_returns_percentage_change(
    db_session: AsyncSession, cockpit_ctx: dict[str, Any],
) -> None:
    today = _today_utc()
    yesterday = today - timedelta(days=1)
    # Seed manuel : 80 hier, 100 aujourd'hui pour STUDENTS_TOTAL
    db_session.add(CockpitKpiSnapshot(
        id=generate_cuid(),
        snapshotDate=yesterday,
        kpiKey=KpiKey.STUDENTS_TOTAL,
        scope=CockpitScope.NATIONAL,
        value=80.0,
        extra={},
    ))
    db_session.add(CockpitKpiSnapshot(
        id=generate_cuid(),
        snapshotDate=today,
        kpiKey=KpiKey.STUDENTS_TOTAL,
        scope=CockpitScope.NATIONAL,
        value=100.0,
        extra={},
    ))
    await db_session.flush()

    service = CockpitService(db_session)
    cmp_ = await service.compare_with_yesterday(KpiKey.STUDENTS_TOTAL)
    assert cmp_.today == 100.0
    assert cmp_.yesterday == 80.0
    assert cmp_.delta == 20.0
    assert cmp_.deltaPercent == 25.0  # +25 %
    assert cmp_.direction == "up"


# ===========================================================================
# 10. RBAC — /kpis/national requiert MINISTRY_ADMIN
# ===========================================================================
@pytest.mark.asyncio
async def test_kpis_endpoint_requires_ministry_admin(
    client: AsyncClient,
    director_headers: dict[str, str],
    ministry_headers: dict[str, str],
) -> None:
    r1 = await client.get("/api/cockpit/kpis/national", headers=director_headers)
    assert r1.status_code == 403

    r2 = await client.get("/api/cockpit/kpis/national", headers=ministry_headers)
    assert r2.status_code == 200


# ===========================================================================
# 11. RBAC — /alerts/top requiert MINISTRY_ADMIN
# ===========================================================================
@pytest.mark.asyncio
async def test_alerts_endpoint_requires_ministry_admin(
    client: AsyncClient,
    director_headers: dict[str, str],
    ministry_headers: dict[str, str],
) -> None:
    r1 = await client.get("/api/cockpit/alerts/top", headers=director_headers)
    assert r1.status_code == 403

    r2 = await client.get("/api/cockpit/alerts/top", headers=ministry_headers)
    assert r2.status_code == 200


# ===========================================================================
# 12. Briefing inclut le top 3 des alertes
# ===========================================================================
@pytest.mark.asyncio
async def test_briefing_includes_top_3_alerts(
    db_session: AsyncSession, cockpit_ctx: dict[str, Any],
) -> None:
    # On force plusieurs anomalies sur la 1ʳᵉ école pour qu'elle remonte
    schools = cockpit_ctx["schools"]
    factories.bind(db_session)
    extras = {schools[0].id: 8, schools[1].id: 5, schools[2].id: 3}
    await _seed_anomalies(
        db_session, cockpit_ctx,
        pending_critical=0, pending_medium=0,
        pending_per_school=extras,
    )
    service = CockpitService(db_session)
    briefing = await service.generate_briefing()
    assert len(briefing.topAlerts) == 3
    # Le 1er doit être l'école avec le plus d'anomalies
    assert briefing.topAlerts[0].schoolId == schools[0].id


# ===========================================================================
# 13. Snapshot idempotent : 2 appels même jour = 5 lignes (pas 10)
# ===========================================================================
@pytest.mark.asyncio
async def test_snapshot_idempotent_same_day(
    db_session: AsyncSession, cockpit_ctx: dict[str, Any],
) -> None:
    service = CockpitService(db_session)
    today = _today_utc()
    await service.snapshot_daily_kpis(snapshot_date=today)
    await service.snapshot_daily_kpis(snapshot_date=today)
    rows = (
        await db_session.execute(
            select(CockpitKpiSnapshot).where(
                CockpitKpiSnapshot.snapshotDate == today,
            ),
        )
    ).scalars().all()
    # pas 2*len(KpiKey) : delete-then-insert garanti.
    assert len(rows) == len(KpiKey)


# ===========================================================================
# 14. Endpoint robuste sur dataset vide
# ===========================================================================
@pytest.mark.asyncio
async def test_kpis_handle_empty_data_gracefully(
    db_session: AsyncSession,
    client: AsyncClient,
    ministry_headers: dict[str, str],
) -> None:
    # Pas de seed : la DB est quasi vide (sauf les users créés par auth_headers)
    response = await client.get(
        "/api/cockpit/kpis/national", headers=ministry_headers,
    )
    assert response.status_code == 200
    body = response.json()
    # Tous les KPI sont à 0, pas d'exception
    assert body["studentsTotal"] == 0
    assert body["attendanceRate"] == 0.0
    assert body["budgetConsumption"] == 0.0
    assert body["criticalAnomaliesOpen"] == 0
    assert body["alertsOpen"] == 0


# ===========================================================================
# 15. Mirror Realtime CRITICAL → cockpit:alert (Module 13 hook)
# ===========================================================================
@pytest.mark.asyncio
async def test_realtime_critical_mirrors_to_cockpit_channel(
    redis_client: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """publish_anomaly(severity=CRITICAL) doit aussi pousser sur cockpit:alert.

    On force le ``get_redis()`` du module realtime à pointer sur la même
    instance que le test (``redis_client``) — la couche realtime publie
    alors sur le canal qu'on écoute ici.
    """
    from app.modules.realtime import service as _rt_service
    from app.modules.realtime.events import CHANNEL_PREFIX
    from app.modules.realtime.service import RealtimeService

    monkeypatch.setattr(_rt_service, "get_redis", lambda: redis_client)

    pubsub = redis_client.pubsub()
    cockpit_channel = f"{CHANNEL_PREFIX}:cockpit:alert"
    try:
        await pubsub.subscribe(cockpit_channel)
        # Skip le message "subscribe"
        msg = await pubsub.get_message(timeout=1.0)
        assert msg is not None and msg["type"] == "subscribe"

        await RealtimeService.publish_anomaly(
            region_id=None,
            anomaly_type="IMPOSSIBLE_GRADE",
            severity="CRITICAL",
            school_id="school-test",
            anomaly_id="anom-test",
        )

        received = None
        for _ in range(20):
            msg = await pubsub.get_message(timeout=0.1)
            if msg and msg.get("type") == "message":
                received = msg
                break
        assert received is not None, "cockpit:alert mirror n'a pas été publié"
        payload = json.loads(received["data"])
        assert payload["payload"]["channel"] == "cockpit:alert"
        assert payload["payload"]["severity"] == "CRITICAL"
    finally:
        await pubsub.unsubscribe(cockpit_channel)
        await pubsub.aclose()
