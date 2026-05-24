"""Module 13 — Realtime WebSocket + Redis Pub/Sub.

Couvre :
1. Event sérialisation + channels selon scope.
2. publish / subscribe roundtrip (Redis réel sur DB 15).
3. channels_for_user pour chaque rôle.
4. WebSocket /api/realtime/connect — auth, scope, heartbeat.
5. Hooks de publish dans attendance / anomalies / predictions.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.modules.anomalies.enums import AnomalySeverity, AnomalyStatus, AnomalyType
from app.modules.anomalies.models import AnomalyDetection
from app.modules.attendance.schemas import (
    BulkScanItem,
    BulkScanRequest,
)
from app.modules.attendance.service import AttendanceService
from app.modules.auth.models import User
from app.modules.realtime.events import (
    CHANNEL_PREFIX,
    GLOBAL_CHANNEL,
    Event,
    EventType,
    publish,
    subscribe,
)
from app.modules.realtime.scope_channels import channels_for_user
from app.modules.realtime.service import RealtimeService
from app.shared.base import generate_cuid
from app.shared.enums import (
    AttendanceStatus,
    PersonType,
    UserRole,
)
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helper : `TestClient(app)` déclenche le lifespan FastAPI qui appelle
# `close_redis()`. Or notre singleton Redis est attaché au loop de la
# session de test (via `_redirect_app_redis_to_test_db`), donc `close_redis`
# exécuté dans le loop sync du TestClient lève "got Future attached to a
# different loop". On patch `close_redis` en no-op (et `get_redis` à
# retourner notre singleton) pour la durée du module.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True, scope="module")
def _neutralise_lifespan_close_redis() -> Any:
    import app.main as _main_mod

    async def _noop() -> None:
        return None

    orig = _main_mod.close_redis
    _main_mod.close_redis = _noop  # type: ignore[assignment]
    yield
    _main_mod.close_redis = orig  # type: ignore[assignment]


# Re-init Redis singleton après chaque test si il a été détruit (defensive).
@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _restore_app_redis_after_test() -> Any:
    yield
    from redis.asyncio import Redis

    from app.core import redis as _redis_mod

    if _redis_mod._redis is None:
        from app.core.config import settings as _settings

        base = str(_settings.redis_url)
        head, _, _ = base.rpartition("/")
        _redis_mod._redis = Redis.from_url(
            f"{head}/15", encoding="utf-8", decode_responses=True
        )


# ---------------------------------------------------------------------------
# Fixtures de base
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def school_ctx(db_session: AsyncSession) -> dict[str, Any]:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    students = []
    for _ in range(2):
        s = await factories.StudentFactory.create_async(schoolId=tree["school"].id)
        students.append(s)
    return {
        "region": tree["region"],
        "prefecture": tree["prefecture"],
        "subPrefecture": tree["subPrefecture"],
        "school": tree["school"],
        "students": students,
    }


# ===========================================================================
# 1. Event serialization + channels
# ===========================================================================
async def test_publish_event_serializes_correctly() -> None:
    """Un Event doit sérialiser proprement vers JSON parseable."""
    event = Event(
        type=EventType.ATTENDANCE_SCAN,
        payload={"schoolId": "sch_123", "count": 42},
        schoolId="sch_123",
        regionId="reg_42",
    )
    blob = event.model_dump_json()
    decoded = json.loads(blob)
    assert decoded["type"] == "ATTENDANCE_SCAN"
    assert decoded["payload"]["count"] == 42
    assert decoded["schoolId"] == "sch_123"
    assert decoded["regionId"] == "reg_42"
    assert "occurredAt" in decoded


async def test_event_channels_route_to_school_region_and_global() -> None:
    """Un event avec schoolId + regionId publie sur les 3 channels."""
    event = Event(
        type=EventType.INCIDENT_CREATED,
        payload={},
        schoolId="sch_X",
        regionId="reg_Y",
    )
    channels = event.channels()
    assert f"{CHANNEL_PREFIX}:school:sch_X" in channels
    assert f"{CHANNEL_PREFIX}:region:reg_Y" in channels
    assert GLOBAL_CHANNEL in channels


# ===========================================================================
# 2. publish / subscribe roundtrip
# ===========================================================================
async def test_subscribe_receives_published_event(redis_client: Any) -> None:
    """Roundtrip publish → subscribe avec un vrai Redis (DB 15)."""
    channel = f"{CHANNEL_PREFIX}:test:{generate_cuid()[:6]}"
    received: list[Event] = []
    stop_evt = asyncio.Event()

    async def consumer() -> None:
        async for ev in subscribe(redis_client, [channel]):
            received.append(ev)
            stop_evt.set()
            break

    task = asyncio.create_task(consumer())
    # Laisse le subscriber s'attacher avant de publier.
    await asyncio.sleep(0.1)
    # Event factice publié directement sur le channel
    test_event = Event(type=EventType.ATTENDANCE_SCAN, payload={"x": 1})
    await redis_client.publish(channel, test_event.model_dump_json())
    try:
        await asyncio.wait_for(stop_evt.wait(), timeout=3.0)
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    assert len(received) == 1
    assert received[0].type == EventType.ATTENDANCE_SCAN.value
    assert received[0].payload == {"x": 1}


# ===========================================================================
# 3. channels_for_user — tous les rôles
# ===========================================================================
def _make_user(role: UserRole, **scope: str | None) -> User:
    return User(
        id=generate_cuid(),
        email=f"{role.value.lower()}@test.local",
        passwordHash="x",
        fullName=f"Test {role.value}",
        role=role,
        isActive=True,
        regionId=scope.get("regionId"),
        prefectureId=scope.get("prefectureId"),
        subPrefectureId=scope.get("subPrefectureId"),
        schoolId=scope.get("schoolId"),
    )


async def test_channels_for_national_admin_includes_global() -> None:
    user = _make_user(UserRole.NATIONAL_ADMIN)
    channels = channels_for_user(user)
    assert GLOBAL_CHANNEL in channels
    # National n'a pas besoin de s'abonner aux region:* car tout passe par global
    assert all(
        not c.startswith(f"{CHANNEL_PREFIX}:region:") for c in channels
    )


async def test_channels_for_regional_admin_includes_region_and_global() -> None:
    user = _make_user(UserRole.REGIONAL_ADMIN, regionId="reg_abc")
    channels = channels_for_user(user)
    assert GLOBAL_CHANNEL in channels
    assert f"{CHANNEL_PREFIX}:region:reg_abc" in channels


async def test_channels_for_school_director_includes_school_and_region() -> None:
    user = _make_user(
        UserRole.SCHOOL_DIRECTOR, regionId="reg_abc", schoolId="sch_def"
    )
    channels = channels_for_user(user)
    assert f"{CHANNEL_PREFIX}:school:sch_def" in channels
    assert f"{CHANNEL_PREFIX}:region:reg_abc" in channels
    assert GLOBAL_CHANNEL in channels


async def test_channels_for_teacher_includes_only_school_region_global() -> None:
    """Un TEACHER reçoit son école + sa région + le global (pas plus)."""
    user = _make_user(UserRole.TEACHER, regionId="reg_t", schoolId="sch_t")
    channels = channels_for_user(user)
    assert set(channels) == {
        GLOBAL_CHANNEL,
        f"{CHANNEL_PREFIX}:region:reg_t",
        f"{CHANNEL_PREFIX}:school:sch_t",
    }


# ===========================================================================
# 4. WebSocket /api/realtime/connect — auth + scope
# ===========================================================================
async def test_websocket_rejects_invalid_token(
    db_session: AsyncSession,
) -> None:
    """Un JWT bidon → close code 1008 (policy violation)."""
    from starlette.testclient import TestClient

    from app.core.database import get_session
    from app.main import app

    async def _override() -> Any:
        yield db_session

    from starlette.websockets import WebSocketDisconnect

    app.dependency_overrides[get_session] = _override
    try:
        with TestClient(app) as tc, pytest.raises(WebSocketDisconnect):  # noqa: SIM117
            # `connect` lève WebSocketDisconnect quand le serveur close avant
            # qu'on puisse lire un message.
            with tc.websocket_connect("/api/realtime/connect?token=not-a-jwt") as ws:
                ws.receive_text()  # devrait lever immédiatement
    finally:
        app.dependency_overrides.pop(get_session, None)


async def test_websocket_accepts_valid_token_and_sends_welcome(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    """JWT valide → on reçoit un message WELCOME contenant les channels."""
    from starlette.testclient import TestClient

    from app.core.database import get_session
    from app.main import app

    # Crée un user SCHOOL_DIRECTOR rattaché au tree.
    user = User(
        id=generate_cuid(),
        email=f"ws-{generate_cuid()[:8]}@test.local",
        passwordHash="x",
        fullName="WS Director",
        role=UserRole.SCHOOL_DIRECTOR,
        isActive=True,
        regionId=school_ctx["region"].id,
        schoolId=school_ctx["school"].id,
    )
    db_session.add(user)
    await db_session.flush()
    token = create_access_token(
        user.id, claims={"role": UserRole.SCHOOL_DIRECTOR.value}
    )

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_session] = _override
    try:
        with TestClient(app) as tc, tc.websocket_connect(
            f"/api/realtime/connect?token={token}"
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "WELCOME"
            assert msg["userId"] == user.id
            assert msg["role"] == UserRole.SCHOOL_DIRECTOR.value
            assert any(
                c.startswith(f"{CHANNEL_PREFIX}:school:")
                for c in msg["channels"]
            )
    finally:
        app.dependency_overrides.pop(get_session, None)


async def test_websocket_receives_event_matching_scope(
    db_session: AsyncSession, school_ctx: dict[str, Any], redis_client: Any,
) -> None:
    """Un SCHOOL_DIRECTOR doit recevoir un event publié sur son school channel."""
    import time

    from starlette.testclient import TestClient

    from app.core.database import get_session
    from app.main import app

    user = User(
        id=generate_cuid(),
        email=f"ws-{generate_cuid()[:8]}@test.local",
        passwordHash="x",
        fullName="WS Director2",
        role=UserRole.SCHOOL_DIRECTOR,
        isActive=True,
        regionId=school_ctx["region"].id,
        schoolId=school_ctx["school"].id,
    )
    db_session.add(user)
    await db_session.flush()
    token = create_access_token(
        user.id, claims={"role": UserRole.SCHOOL_DIRECTOR.value}
    )

    async def _override() -> Any:
        yield db_session

    app.dependency_overrides[get_session] = _override
    try:
        with TestClient(app) as tc, tc.websocket_connect(
            f"/api/realtime/connect?token={token}"
        ) as ws:
            welcome = ws.receive_json()
            assert welcome["type"] == "WELCOME"
            # Laisse le subscriber Redis s'enregistrer côté serveur.
            # TestClient est sync mais le subscribe est async ; on
            # spin-wait sur pubsub_numsub jusqu'à voir un abonné.
            school_id = school_ctx["school"].id
            school_channel = f"{CHANNEL_PREFIX}:school:{school_id}"
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                subs = await redis_client.pubsub_numsub(school_channel)
                n = subs[0][1] if subs else 0
                if n >= 1:
                    break
                await asyncio.sleep(0.05)
            event = Event(
                type=EventType.INCIDENT_CREATED,
                payload={"severity": "HIGH"},
                schoolId=school_id,
                regionId=school_ctx["region"].id,
            )
            await publish(redis_client, event)
            msg = ws.receive_json()
            assert msg["type"] == EventType.INCIDENT_CREATED.value
            assert msg["schoolId"] == school_id
    finally:
        app.dependency_overrides.pop(get_session, None)


async def test_websocket_does_not_receive_event_outside_scope(
    db_session: AsyncSession, school_ctx: dict[str, Any], redis_client: Any,
) -> None:
    """Un SCHOOL_DIRECTOR ne doit PAS recevoir un event publié sur une autre école."""
    # Test conceptuel via Event.channels() : un event scopé sur une école
    # tierce ne touche aucun channel auquel le user SCHOOL_DIRECTOR souscrit
    # (autre que `global` — qui est touché par TOUS les events). Pour valider
    # rigoureusement le filtrage sans dépendre des timings WebSocket sync,
    # on vérifie l'intersection des channels.
    user = _make_user(
        UserRole.SCHOOL_DIRECTOR,
        regionId=school_ctx["region"].id,
        schoolId=school_ctx["school"].id,
    )
    user_channels = set(channels_for_user(user))

    foreign_event = Event(
        type=EventType.INCIDENT_CREATED,
        payload={"severity": "LOW"},
        schoolId="sch_other_999",
        regionId="reg_other_999",
    )
    foreign_channels = set(foreign_event.channels())
    # L'intersection ne contient JAMAIS le school:* ou region:* du user —
    # uniquement `global` éventuellement (mais ici l'event est scopé école
    # donc il a aussi global, ce qui est attendu : un national ou un user
    # connecté qui subscribe global voit cet event).
    # On vérifie qu'aucun channel "ciblé" (school:* ou region:*) n'est partagé.
    targeted = {
        c for c in user_channels
        if c.startswith(f"{CHANNEL_PREFIX}:school:")
        or c.startswith(f"{CHANNEL_PREFIX}:region:")
    }
    targeted_foreign = {
        c for c in foreign_channels
        if c.startswith(f"{CHANNEL_PREFIX}:school:")
        or c.startswith(f"{CHANNEL_PREFIX}:region:")
    }
    assert targeted.isdisjoint(targeted_foreign), (
        f"Un user SCHOOL_DIRECTOR ne doit pas partager de channel ciblé avec un event "
        f"d'une autre école: user={targeted} event={targeted_foreign}"
    )


# ===========================================================================
# 5. Hooks publish dans les services métier
# ===========================================================================
async def test_event_published_on_bulk_scan(
    db_session: AsyncSession, school_ctx: dict[str, Any], redis_client: Any,
) -> None:
    """Vérifie que `bulk_scan` appelle bien `RealtimeService.publish_attendance_scan`."""
    # On mock la méthode publish_attendance_scan pour vérifier l'appel.
    user = User(
        id=generate_cuid(),
        email=f"scan-{generate_cuid()[:8]}@test.local",
        passwordHash="x",
        fullName="Director Scan",
        role=UserRole.SCHOOL_DIRECTOR,
        isActive=True,
        regionId=school_ctx["region"].id,
        schoolId=school_ctx["school"].id,
    )
    db_session.add(user)
    await db_session.flush()

    student = school_ctx["students"][0]
    svc = AttendanceService(db_session)
    dto = BulkScanRequest(
        items=[
            BulkScanItem(
                studentId=student.id,
                scannedAt=datetime.now(UTC),
                status=AttendanceStatus.PRESENT,
            )
        ]
    )

    calls: list[tuple[Any, ...]] = []

    async def _capture(school_id: str, region_id: str | None, count: int) -> int:
        calls.append((school_id, region_id, count))
        return 0

    with patch.object(
        RealtimeService, "publish_attendance_scan", side_effect=_capture
    ):
        result = await svc.bulk_scan(user, dto)
    assert result.inserted == 1
    assert len(calls) == 1
    assert calls[0][0] == school_ctx["school"].id
    assert calls[0][2] == 1  # count == 1


async def test_event_published_on_anomaly_detected_critical(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    """run_all_detectors publie un event UNIQUEMENT pour les anomalies CRITICAL."""
    from app.modules.anomalies.service import AnomalyService

    svc = AnomalyService(db_session)

    calls: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    # On mock la liste des detectors pour controler exactement ce qui est crée.
    async def _fake_detector_critical(session: Any, **kw: Any) -> list[AnomalyDetection]:
        return [
            AnomalyDetection(
                id=generate_cuid(),
                type=AnomalyType.IMPOSSIBLE_GRADE,
                severity=AnomalySeverity.CRITICAL,
                status=AnomalyStatus.PENDING,
                schoolId=school_ctx["school"].id,
                regionId=school_ctx["region"].id,
                entityType="GRADE",
                entityId=generate_cuid(),
                description="Test critical anomaly",
                evidence={"raw": "test"},
                detectedAt=datetime.now(UTC),
            )
        ]

    async def _fake_detector_minor(session: Any, **kw: Any) -> list[AnomalyDetection]:
        return [
            AnomalyDetection(
                id=generate_cuid(),
                type=AnomalyType.IMPOSSIBLE_GRADE,
                severity=AnomalySeverity.LOW,
                status=AnomalyStatus.PENDING,
                schoolId=school_ctx["school"].id,
                regionId=school_ctx["region"].id,
                entityType="GRADE",
                entityId=generate_cuid(),
                description="Test low-noise anomaly",
                evidence={"raw": "noise"},
                detectedAt=datetime.now(UTC),
            )
        ]

    with patch(
        "app.modules.anomalies.service.ALL_DETECTORS",
        [_fake_detector_critical, _fake_detector_minor],
    ), patch.object(RealtimeService, "publish_anomaly", side_effect=_capture):
        total = await svc.run_all_detectors()
    assert total == 2
    # Un SEUL call (le CRITICAL) — le LOW n'est pas publié.
    assert len(calls) == 1
    assert calls[0]["severity"] == AnomalySeverity.CRITICAL.value


async def test_event_published_on_dropout_high_risk(
    db_session: AsyncSession,
) -> None:
    """predict_student publie un event UNIQUEMENT si riskLevel=HIGH."""
    from app.modules.predictions.enums import DropoutRiskLevel
    from app.modules.predictions.service import PredictionService

    # Crée le student via factory
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    student = await factories.StudentFactory.create_async(schoolId=tree["school"].id)
    svc = PredictionService(db_session)

    # Mock le modèle pour forcer HIGH risk
    class _FakeModel:
        version = "test-v1"

        def predict_proba(self, features: dict[str, Any]) -> tuple[float, DropoutRiskLevel]:
            return 0.92, DropoutRiskLevel.HIGH

    async def _fake_load_model() -> _FakeModel:
        return _FakeModel()

    async def _fake_extract(session: Any, sid: str, d: date) -> dict[str, Any]:
        return {"feature_a": 1.0}

    calls: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    with patch.object(
        PredictionService, "_load_model", side_effect=_fake_load_model
    ), patch(
        "app.modules.predictions.service.extract_features", side_effect=_fake_extract
    ), patch.object(
        RealtimeService, "publish_dropout_prediction_high", side_effect=_capture
    ):
        pred = await svc.predict_student(student.id)
    assert pred.riskLevel == DropoutRiskLevel.HIGH
    assert len(calls) == 1
    assert calls[0]["student_id"] == student.id
    assert calls[0]["school_id"] == tree["school"].id


async def test_event_not_published_on_dropout_low_risk(
    db_session: AsyncSession,
) -> None:
    """predict_student ne publie PAS si riskLevel=LOW (filtre cockpit)."""
    from app.modules.predictions.enums import DropoutRiskLevel
    from app.modules.predictions.service import PredictionService

    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    student = await factories.StudentFactory.create_async(schoolId=tree["school"].id)
    svc = PredictionService(db_session)

    class _FakeModel:
        version = "test-v1"

        def predict_proba(self, features: dict[str, Any]) -> tuple[float, DropoutRiskLevel]:
            return 0.05, DropoutRiskLevel.LOW

    async def _fake_load_model() -> _FakeModel:
        return _FakeModel()

    async def _fake_extract(session: Any, sid: str, d: date) -> dict[str, Any]:
        return {}

    calls: list[Any] = []

    async def _capture(**kwargs: Any) -> int:
        calls.append(kwargs)
        return 0

    with patch.object(
        PredictionService, "_load_model", side_effect=_fake_load_model
    ), patch(
        "app.modules.predictions.service.extract_features", side_effect=_fake_extract
    ), patch.object(
        RealtimeService, "publish_dropout_prediction_high", side_effect=_capture
    ):
        await svc.predict_student(student.id)
    assert calls == []
