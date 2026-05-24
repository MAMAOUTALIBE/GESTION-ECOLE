"""Module 15 — admin / settings plateforme.

Couvre :

1.  test_set_and_get_setting_string         — round-trip string
2.  test_set_setting_typed_int              — typage int préservé
3.  test_setting_change_creates_audit_log   — audit log à chaque write
4.  test_setting_cache_invalidated_on_update — cache Redis cohérent
5.  test_feature_flag_disabled_returns_false — flag off → False
6.  test_feature_flag_rollout_50pct_is_stable_per_user — déterministe
7.  test_feature_flag_rollout_100pct_all_users_enabled — full rollout
8.  test_maintenance_mode_blocks_writes_returns_503     — 503 sur POST
9.  test_maintenance_mode_allows_reads        — GET passe
10. test_settings_endpoint_requires_ministry_role — RBAC
11. test_changes_endpoint_returns_audit_history  — audit visible
12. test_disable_maintenance_unblocks_writes    — cleanup OK
13. test_setting_value_validates_type          — typage refusé
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.models import SettingChangeLog
from app.modules.admin.service import (
    MAINTENANCE_REDIS_KEY,
    SETTING_CACHE_PREFIX,
    AdminService,
)
from app.shared.enums import UserRole

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def _clean_admin_redis_keys(redis_client: Redis):
    """Purge les clés admin avant chaque test (sécurité supplémentaire)."""
    pattern_prefixes = (SETTING_CACHE_PREFIX, MAINTENANCE_REDIS_KEY)
    for prefix in pattern_prefixes:
        async for key in redis_client.scan_iter(match=f"{prefix}*"):
            await redis_client.delete(key)
    yield
    for prefix in pattern_prefixes:
        async for key in redis_client.scan_iter(match=f"{prefix}*"):
            await redis_client.delete(key)


# ===========================================================================
# 1. Round-trip string
# ===========================================================================
@pytest.mark.asyncio
async def test_set_and_get_setting_string(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    await svc.set_setting("hello.message", "bonjour", type_="string", actor_id=None)
    value = await svc.get_setting("hello.message")
    assert value == "bonjour"


# ===========================================================================
# 2. Typage int préservé
# ===========================================================================
@pytest.mark.asyncio
async def test_set_setting_typed_int(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    await svc.set_setting("limits.max_students", 5000, type_="int")
    value = await svc.get_setting("limits.max_students")
    assert value == 5000
    assert isinstance(value, int)


# ===========================================================================
# 3. Audit log à chaque write
# ===========================================================================
@pytest.mark.asyncio
async def test_setting_change_creates_audit_log(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    await svc.set_setting("audit.test_key", "v1", type_="string", actor_id="user-1")
    await svc.set_setting("audit.test_key", "v2", type_="string", actor_id="user-2")

    rows = (await db_session.execute(
        select(SettingChangeLog).where(SettingChangeLog.key == "audit.test_key")
        .order_by(SettingChangeLog.changedAt.asc())
    )).scalars().all()
    assert len(rows) == 2
    assert rows[0].newValue == "v1"
    assert rows[0].oldValue is None
    assert rows[0].changedById == "user-1"
    assert rows[1].oldValue == "v1"
    assert rows[1].newValue == "v2"
    assert rows[1].changedById == "user-2"


# ===========================================================================
# 4. Cache Redis cohérent (read-after-write dans le même thread)
# ===========================================================================
@pytest.mark.asyncio
async def test_setting_cache_invalidated_on_update(
    db_session: AsyncSession, redis_client: Redis,
) -> None:
    svc = AdminService(db_session, redis=redis_client)
    await svc.set_setting("cache.key1", "first", type_="string")
    # Première lecture → remplit le cache
    assert await svc.get_setting("cache.key1") == "first"
    cached = await redis_client.get(f"{SETTING_CACHE_PREFIX}cache.key1")
    assert cached is not None

    # Update → cache doit être invalidé
    await svc.set_setting("cache.key1", "second", type_="string")
    cached_after = await redis_client.get(f"{SETTING_CACHE_PREFIX}cache.key1")
    assert cached_after is None

    # Read-after-write doit voir la nouvelle valeur
    assert await svc.get_setting("cache.key1") == "second"


# ===========================================================================
# 5. Flag disabled → False
# ===========================================================================
@pytest.mark.asyncio
async def test_feature_flag_disabled_returns_false(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    await svc.set_feature_flag("ff.disabled", enabled=False, rollout_percentage=100)
    for uid in ("alice", "bob", "charlie"):
        assert await svc.is_feature_enabled_for_user("ff.disabled", uid) is False


# ===========================================================================
# 6. Rollout 50 % stable par user
# ===========================================================================
@pytest.mark.asyncio
async def test_feature_flag_rollout_50pct_is_stable_per_user(
    db_session: AsyncSession,
) -> None:
    svc = AdminService(db_session)
    await svc.set_feature_flag("ff.canary", enabled=True, rollout_percentage=50)

    # Pour un même user, la réponse doit être stable entre appels.
    user_ids = [f"user-{i:04d}" for i in range(200)]
    first_pass = {
        uid: await svc.is_feature_enabled_for_user("ff.canary", uid)
        for uid in user_ids
    }
    second_pass = {
        uid: await svc.is_feature_enabled_for_user("ff.canary", uid)
        for uid in user_ids
    }
    assert first_pass == second_pass

    # Et la distribution doit être ~50 % (±15 % de marge à 200 users).
    enabled_count = sum(1 for v in first_pass.values() if v)
    assert 70 <= enabled_count <= 130, f"got {enabled_count}/200 enabled"


# ===========================================================================
# 7. Rollout 100 % → tous les users
# ===========================================================================
@pytest.mark.asyncio
async def test_feature_flag_rollout_100pct_all_users_enabled(
    db_session: AsyncSession,
) -> None:
    svc = AdminService(db_session)
    await svc.set_feature_flag("ff.full", enabled=True, rollout_percentage=100)
    for uid in (f"u-{i}" for i in range(50)):
        assert await svc.is_feature_enabled_for_user("ff.full", uid) is True


# ===========================================================================
# 8. Maintenance bloque les écritures (503)
# ===========================================================================
@pytest.mark.asyncio
async def test_maintenance_mode_blocks_writes_returns_503(
    client: AsyncClient, auth_headers, db_session: AsyncSession,
) -> None:
    # Active la maintenance via le service
    svc = AdminService(db_session)
    await svc.enable_maintenance_mode(actor_id=None)

    # Un POST quelconque (ici /api/auth/logout qui exige juste un token) doit
    # se prendre un 503. On utilise un token valide pour bien tester le
    # middleware (et pas un 401).
    headers = await auth_headers(UserRole.TEACHER)
    r = await client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["code"] == "maintenance_mode"

    # Cleanup
    await svc.disable_maintenance_mode(actor_id=None)


# ===========================================================================
# 9. Maintenance laisse passer les GET
# ===========================================================================
@pytest.mark.asyncio
async def test_maintenance_mode_allows_reads(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    svc = AdminService(db_session)
    await svc.enable_maintenance_mode(actor_id=None)
    try:
        r = await client.get("/health")
        assert r.status_code == 200
    finally:
        await svc.disable_maintenance_mode(actor_id=None)


# ===========================================================================
# 10. RBAC : un TEACHER ne peut pas lire /settings
# ===========================================================================
@pytest.mark.asyncio
async def test_settings_endpoint_requires_ministry_role(
    client: AsyncClient, auth_headers,
) -> None:
    teacher_headers = await auth_headers(UserRole.TEACHER)
    r_forbidden = await client.get("/api/admin/settings", headers=teacher_headers)
    assert r_forbidden.status_code == 403, r_forbidden.text

    ministry_headers = await auth_headers(UserRole.MINISTRY_ADMIN)
    r_ok = await client.get("/api/admin/settings", headers=ministry_headers)
    assert r_ok.status_code == 200, r_ok.text
    assert isinstance(r_ok.json(), list)


# ===========================================================================
# 11. /changes renvoie l'historique
# ===========================================================================
@pytest.mark.asyncio
async def test_changes_endpoint_returns_audit_history(
    client: AsyncClient, auth_headers, db_session: AsyncSession,
) -> None:
    import asyncio

    svc = AdminService(db_session)
    await svc.set_setting("history.k", "v1", type_="string", actor_id="admin-1")
    # Petit gap pour que changedAt diffère d'au moins une milliseconde —
    # sinon `func.now()` retourne souvent le timestamp de transaction
    # PostgreSQL, identique entre deux COMMIT proches.
    await asyncio.sleep(0.05)
    await svc.set_setting("history.k", "v2", type_="string", actor_id="admin-1")

    ministry_headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    r = await client.get("/api/admin/changes?key=history.k", headers=ministry_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 2
    # Vérifie que les deux valeurs sont présentes (peu importe l'ordre exact
    # quand les deux writes tombent dans la même tick PostgreSQL).
    new_values = {row["newValue"] for row in body}
    assert new_values == {"v1", "v2"}
    assert all(row["kind"] == "SETTING" for row in body)


# ===========================================================================
# 12. Disable maintenance débloque
# ===========================================================================
@pytest.mark.asyncio
async def test_disable_maintenance_unblocks_writes(
    client: AsyncClient, auth_headers, db_session: AsyncSession,
) -> None:
    svc = AdminService(db_session)
    await svc.enable_maintenance_mode(actor_id=None)

    headers = await auth_headers(UserRole.TEACHER)
    r_blocked = await client.post("/api/auth/logout", headers=headers)
    assert r_blocked.status_code == 503

    await svc.disable_maintenance_mode(actor_id=None)

    # Réémet le même POST (avec un user frais — l'ancien token a été
    # blacklist au logout précédent ? non, le 503 a court-circuité l'app).
    r_ok = await client.post("/api/auth/logout", headers=headers)
    # Logout doit aboutir (204 ou 200 selon l'API). Tout sauf 503 prouve
    # que la maintenance a bien été levée.
    assert r_ok.status_code != 503, r_ok.text


# ===========================================================================
# 13. Validation typée à l'écriture
# ===========================================================================
@pytest.mark.asyncio
async def test_setting_value_validates_type(db_session: AsyncSession) -> None:
    svc = AdminService(db_session)
    # On insère d'abord un int → puis on tente d'overwrite avec un string en
    # gardant le même type → doit lever ValidationFailedError.
    await svc.set_setting("typed.int", 42, type_="int")
    from app.core.exceptions import ValidationFailedError
    with pytest.raises(ValidationFailedError):
        await svc.set_setting("typed.int", "not-an-int", type_="int")

    # Pareil pour boolean (0 et 1 ne doivent PAS passer pour bool)
    with pytest.raises(ValidationFailedError):
        await svc.set_setting("typed.bool", 1, type_="boolean")

    # Mais boolean True passe :
    await svc.set_setting("typed.bool", True, type_="boolean")
    assert await svc.get_setting("typed.bool") is True
