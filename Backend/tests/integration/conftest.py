"""Integration test fixtures (Module 0).

Strategie generale
------------------
* `db_engine` est session-scoped : il pointe vers la base `gestionee_test`,
  cree toutes les tables au demarrage (`Base.metadata.create_all`) et les
  drop en fin de session. On ne touche PAS a l'extension PostGIS (si elle
  est presente, tant mieux ; si elle est absente, on neutralise la colonne
  `School.geom` pour permettre a `create_all` de passer — les tests qui
  dependent reellement de PostGIS doivent etre marques `@pytest.mark.postgis`
  et ils seront automatiquement skip).
* `db_session` est function-scoped : chaque test re-utilise une transaction
  qui est rollback a la fin, garantissant une isolation totale entre tests
  sans avoir a recreer le schema a chaque fois.
* `client` injecte un override de `get_session` pour que FastAPI utilise la
  session du test (et donc voie les data crees par les factories).
* `auth_headers(role=...)` factory cree un User en DB pour le role demande,
  forge un JWT signe avec la cle de l'app et renvoie l'en-tete Authorization.
* `redis_client` cible la DB 15 (jamais utilisee par dev/Celery) et FLUSHDB
  apres chaque test.

Quand l'equipe migrera vers TestContainers
------------------------------------------
Cette conftest est volontairement decouplee : il suffira de remplacer la
fonction `_test_database_url()` par une qui demande l'URL au container
Postgres demarre par testcontainers-python (et `_test_redis_url()` pour
Redis), sans toucher aux fixtures aval. Le reste du code (factories,
auth_headers, client) restera identique.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.database import get_session
from app.core.security import create_access_token
from app.main import app

# Import the modules registry so every model is registered on Base.metadata
# *before* we ask create_all() to materialise it.
from app.modules import *  # noqa: F401,F403
from app.modules.auth.models import User
from app.shared.base import Base, generate_cuid
from app.shared.enums import UserRole

# Test isolation knobs --------------------------------------------------------
TEST_DB_NAME = "gestionee_test"
TEST_REDIS_DB = 15  # DB jamais utilisee par dev/Celery


# ---------------------------------------------------------------------------
# Redis singleton — force the app's global Redis client (used by
# `get_current_user` for the JTI blacklist) to point at DB 15. Without this
# the app would write `auth:revoked:*` and `rl:*` keys to DB 0 (dev/Celery).
# Done before the first test runs.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _redirect_app_redis_to_test_db() -> None:
    from app.core import redis as _redis_mod

    # Force `get_redis()` to instantiate against DB 15 regardless of env.
    _redis_mod._redis = Redis.from_url(
        _test_redis_url(), encoding="utf-8", decode_responses=True
    )


# ---------------------------------------------------------------------------
# Argon2 — relaxed cost in tests (~1ms per hash vs 200ms in prod).
# In production we use time_cost=3, memory_cost=64*1024, parallelism=4 which
# is intentionally slow for security. That makes every test that calls
# `hash_password` or any Argon2 path 200x slower. For Module 1, the auth
# tests alone hash dozens of times per test (password history, MFA recovery
# codes), so we monkeypatch the shared PasswordHasher to a fast profile.
# This is autouse session-scoped — applies to every test run with no opt-in.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _fast_argon2_for_tests() -> None:
    """Swap the security module's PasswordHasher to a fast-test profile."""
    from app.core import security as _security

    _security._argon2 = PasswordHasher(
        time_cost=1, memory_cost=8, parallelism=1, hash_len=32, salt_len=8
    )


def _swap_db_name(url: str, new_db: str) -> str:
    """Remplace le dernier segment "/<dbname>" par "/<new_db>".

    On evite pydantic.PostgresDsn ici : la rewrite est purement textuelle
    et tolere les formats `postgresql+asyncpg://...` comme `postgresql://...`.
    """
    # Sanity : on attend une URL avec au moins un "/" apres l'host.
    head, _, _ = url.rpartition("/")
    return f"{head}/{new_db}"


def _test_database_url() -> str:
    """URL async vers la DB de test, derivee de DATABASE_URL.

    Hook a remplacer le jour ou on migre vers testcontainers-python.
    """
    return _swap_db_name(str(settings.database_url), TEST_DB_NAME)


def _test_redis_url() -> str:
    base = str(settings.redis_url)
    head, _, _ = base.rpartition("/")
    return f"{head}/{TEST_REDIS_DB}"


# ---------------------------------------------------------------------------
# PostGIS shim — si l'extension n'est pas installee sur le serveur, on
# remplace temporairement le type de la colonne School.geom par un Text
# nullable pour que `create_all` puisse construire le schema. Les tests qui
# ont reellement besoin de PostGIS doivent etre marques `@pytest.mark.postgis`
# (auto-skip si POSTGIS_AVAILABLE est faux).
# ---------------------------------------------------------------------------
async def _postgis_available(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        row = await conn.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
        )
        return row.scalar() is not None


def _neutralise_postgis_columns() -> tuple[list[tuple], list[tuple]]:
    """Swap Geography columns with plain Text so create_all() works.

    Effet de bord supplementaire : on supprime aussi les indexes GIST qui
    visent ces colonnes (un GIST sur Text n'a pas d'operator class).

    Renvoie deux listes :
    * (table_name, column_name, original_type) — pour restaurer les colonnes.
    * (table_name, index) — pour restaurer les indexes supprimes.
    """
    from sqlalchemy import Text

    swapped_cols: list[tuple] = []
    spatial_col_names: set[tuple[str, str]] = set()
    for table in Base.metadata.tables.values():
        for column in list(table.columns):
            type_cls_name = type(column.type).__name__
            if type_cls_name in {"Geography", "Geometry"}:
                swapped_cols.append((table.name, column.name, column.type))
                column.type = Text()
                spatial_col_names.add((table.name, column.name))

    # Drop des indexes GIST/SPGIST visant les colonnes spatiales.
    # Note: ``ix.dialect_options['postgresql']['using']`` peut renvoyer ``False``
    # (le defaut SQLAlchemy quand on n'a pas explicitement passe ``using=``
    # mais qu'on a passe au moins une autre option `postgresql_*` comme
    # ``postgresql_where``). On normalise en str avant ``.lower()`` pour
    # eviter ``AttributeError: 'bool' object has no attribute 'lower'``.
    removed_indexes: list[tuple] = []
    for table in Base.metadata.tables.values():
        for ix in list(table.indexes):
            using_value = ix.dialect_options.get("postgresql", {}).get("using", "")
            uses_postgresql_gist = (
                str(using_value or "").lower() in {"gist", "spgist"}
            )
            touches_spatial = any(
                (table.name, c.name) in spatial_col_names for c in ix.columns
            )
            if uses_postgresql_gist or touches_spatial:
                table.indexes.discard(ix)
                removed_indexes.append((table.name, ix))
    return swapped_cols, removed_indexes


def _restore_postgis_columns(
    swapped_cols: list[tuple], removed_indexes: list[tuple]
) -> None:
    for table_name, column_name, original_type in swapped_cols:
        table = Base.metadata.tables[table_name]
        table.columns[column_name].type = original_type
    for table_name, index in removed_indexes:
        table = Base.metadata.tables[table_name]
        table.indexes.add(index)


# ---------------------------------------------------------------------------
# Engine & schema (session-scoped)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Engine async pointant vers la DB de test.

    * Cree toutes les tables au demarrage.
    * Detecte PostGIS et expose la capability via `pytest.postgis_available`
      (utilise par le hook collection_modifyitems pour auto-skip les tests).
    * Drop toutes les tables en fin de session.
    """
    url = _test_database_url()
    engine = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        future=True,
    )

    has_postgis = await _postgis_available(engine)
    pytest.postgis_available = has_postgis  # type: ignore[attr-defined]

    swapped_cols: list[tuple] = []
    removed_indexes: list[tuple] = []
    if not has_postgis:
        swapped_cols, removed_indexes = _neutralise_postgis_columns()

    async with engine.begin() as conn:
        # Module 2 — pg_trgm requis pour le service de dédoublonnage
        # (utilise func.similarity). On l'active idempotemment ici pour
        # que les tests d'integration puissent appeler check_duplicates
        # sans dependre de l'execution prealable de la migration 0002.
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        except Exception:  # pragma: no cover - depends on perms
            pass
        # PRECAUTION : drop d'abord pour repartir d'un schema propre meme si
        # la session precedente s'est terminee brutalement.
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield engine
    finally:
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        finally:
            await engine.dispose()
            if swapped_cols or removed_indexes:
                _restore_postgis_columns(swapped_cols, removed_indexes)


# ---------------------------------------------------------------------------
# Session per test (with rollback for isolation)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Yield une AsyncSession enrobee dans une transaction rollback-only.

    Implementation "SAVEPOINT re-open" classique de SQLAlchemy : on ouvre
    une connexion + une transaction au niveau outer, on relie une session
    a cette connexion, et on ouvre un nested savepoint qu'on re-cree a
    chaque commit demande par le code applicatif. En fin de test, on
    rollback la transaction outer : tout disparait, y compris ce que le
    service a "commit".
    """
    connection: AsyncConnection = await db_engine.connect()
    outer_trans = await connection.begin()

    session_factory = async_sessionmaker(
        bind=connection,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
        join_transaction_mode="create_savepoint",
    )
    session = session_factory()

    # Re-open a savepoint each time the inner one is released, so that
    # `session.commit()` calls inside services don't end the outer trans.
    sync_session = session.sync_session

    @event.listens_for(sync_session, "after_transaction_end")
    def _restart_savepoint(sess, trans):  # type: ignore[no-untyped-def]
        if trans.nested and not trans._parent.nested:  # type: ignore[attr-defined]
            sess.begin_nested()

    sync_session.begin_nested()

    try:
        yield session
    finally:
        await session.close()
        if outer_trans.is_active:
            await outer_trans.rollback()
        await connection.close()


# ---------------------------------------------------------------------------
# Redis (DB 15, FLUSHDB after each test)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def redis_client() -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(
        _test_redis_url(),
        encoding="utf-8",
        decode_responses=True,
    )
    try:
        # Sanity check : si Redis n'est pas dispo, on skip plutot que de
        # faire planter le test avec une trace verbeuse.
        try:
            await client.ping()
        except Exception as exc:  # pragma: no cover - depends on env
            pytest.skip(f"Redis indisponible ({exc}) — skip tests Redis.")
        yield client
    finally:
        try:
            await client.flushdb()
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# Autouse cleanup: flush Redis DB 15 between EVERY test. This is critical
# for Module 1 — the rate limiter writes to per-IP / per-email keys that
# would otherwise leak between tests (e.g. one rate-limit test would block
# every subsequent test using "127.0.0.1").
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _flush_redis_per_test() -> AsyncIterator[None]:
    yield
    try:
        client: Redis = Redis.from_url(
            _test_redis_url(), encoding="utf-8", decode_responses=True
        )
        try:
            await client.flushdb()
        finally:
            await client.aclose()
    except Exception:  # pragma: no cover - depends on env
        pass


# ---------------------------------------------------------------------------
# HTTP client with DB override
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """AsyncClient httpx pointant vers l'app FastAPI in-process.

    On override `get_session` pour que les routers utilisent la SAME session
    que le test (les data crees par les factories sont visibles cote API).
    Redis (DB 15) est deja rediscrit globalement par `_redirect_app_redis_to_test_db`.
    """

    async def _get_session_override() -> AsyncIterator[AsyncSession]:
        # IMPORTANT : on ne commit/rollback pas ici ; la gestion de la
        # transaction est faite par la fixture `db_session`.
        yield db_session

    app.dependency_overrides[get_session] = _get_session_override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# Auth helper factory
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def auth_headers(
    db_session: AsyncSession,
) -> Callable[..., "_AwaitableHeaders"]:
    """Factory async qui cree un User en DB et renvoie l'en-tete Bearer.

    Usage dans un test :

        async def test_xxx(client, auth_headers):
            headers = await auth_headers(UserRole.SCHOOL_DIRECTOR)
            r = await client.get("/api/auth/me", headers=headers)

    Le User est minimal (pas de region/school/etc.). Les tests qui doivent
    rattacher l'utilisateur a une entite territoriale peuvent passer les ids
    via les kwargs (regionId=..., prefectureId=..., subPrefectureId=...,
    schoolId=...).
    """

    async def _make(
        role: UserRole | str,
        *,
        email: str | None = None,
        full_name: str | None = None,
        is_active: bool = True,
        regionId: str | None = None,
        prefectureId: str | None = None,
        subPrefectureId: str | None = None,
        schoolId: str | None = None,
    ) -> dict[str, str]:
        from app.core.security import hash_password

        role_value = role.value if isinstance(role, UserRole) else str(role)
        uid = generate_cuid()
        user = User(
            id=uid,
            email=email or f"{role_value.lower()}-{uid[:8]}@test.local",
            passwordHash=hash_password("Test@Pa55word!"),
            fullName=full_name or f"Test {role_value}",
            role=UserRole(role_value),
            isActive=is_active,
            regionId=regionId,
            prefectureId=prefectureId,
            subPrefectureId=subPrefectureId,
            schoolId=schoolId,
        )
        db_session.add(user)
        await db_session.flush()  # pas de commit : la session est trans-only

        token = create_access_token(
            user.id,
            claims={
                "role": role_value,
                "regionId": user.regionId,
                "prefectureId": user.prefectureId,
                "subPrefectureId": user.subPrefectureId,
                "schoolId": user.schoolId,
            },
        )
        return {"Authorization": f"Bearer {token}"}

    return _make  # type: ignore[return-value]


# Type alias used in the signature above to keep mypy quiet without forcing
# tests to import a real type.
_AwaitableHeaders = dict[str, str]


# ---------------------------------------------------------------------------
# Hooks pytest
# ---------------------------------------------------------------------------
def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    """Auto-skip les tests marques `postgis` si l'extension n'est pas presente.

    La capability est detectee soit par la fixture `db_engine` (au demarrage
    de la session), soit par une probe synchrone psycopg2 ci-dessous (qui
    intervient tres tot pendant la collection).
    """
    if getattr(pytest, "postgis_available", None) is None:
        try:
            import psycopg2  # type: ignore[import-untyped]

            sync_url = str(settings.database_url_sync)
            sync_url = _swap_db_name(sync_url, TEST_DB_NAME)
            # psycopg2 attend un DSN classique
            conn = psycopg2.connect(sync_url.replace("postgresql+psycopg2://", "postgresql://"))
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM pg_extension WHERE extname='postgis'")
                    pytest.postgis_available = cur.fetchone() is not None  # type: ignore[attr-defined]
            finally:
                conn.close()
        except Exception:
            pytest.postgis_available = False  # type: ignore[attr-defined]

    if pytest.postgis_available:  # type: ignore[attr-defined]
        return

    skip_no_gis = pytest.mark.skip(reason="PostGIS not installed on this server")
    for item in items:
        if "postgis" in item.keywords:
            item.add_marker(skip_no_gis)


def pytest_configure(config):  # type: ignore[no-untyped-def]
    config.addinivalue_line(
        "markers",
        "postgis: marker for tests that require PostGIS to run (auto-skip if absent).",
    )
    config.addinivalue_line(
        "markers",
        "integration: marker for tests touching DB/Redis (vs pure unit tests).",
    )
