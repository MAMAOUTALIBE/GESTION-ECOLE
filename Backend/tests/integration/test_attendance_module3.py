"""Module 3 — Attendance : bulk scan, stats, partitionnement déclaratif.

Couvre 15+ cas répartis sur 5 axes :
* Partitionnement (4 tests) : table partitionnée, routing par partition,
  default catch-all, idempotence ensure_future_partitions.
* Bulk scan (4 tests) : ≤ 200 records, refus du futur, idempotence par
  jour, partial failure -> erreurs typées.
* Stats (4 tests) : group by day, filtre par école, cache Redis, scope.
* RBAC (2 tests) : partitions = national admin, bulk = directeur.

Fixture clé : ``attendance_partitioned_table`` — drop la table créée par
``create_all`` puis la recrée en partitionnée + 4 partitions (mois courant
+ 3 suivants + ``_default``). Indispensable dès qu'on interroge
``pg_inherits`` ou qu'on vérifie le routing d'une insertion vers une
partition spécifique.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.attendance.models import AttendanceRecord
from app.modules.attendance.partitions import (
    ensure_future_partitions,
    list_partitions,
    make_partition_sql,
    partition_name,
)
from app.modules.attendance.service import STATS_CACHE_PREFIX
from app.shared.enums import AttendanceStatus, PersonType, UserRole
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures locales
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def attendance_partitioned_table(db_session: AsyncSession) -> AsyncSession:
    """Recrée ``AttendanceRecord`` partitionnée à la place de la version
    monolithique créée par ``Base.metadata.create_all``.

    Note importante : la fixture ``db_session`` enveloppe tout dans un
    SAVEPOINT rollback ; les DDL (DROP TABLE / CREATE TABLE) commits
    immédiat n'existent pas en Postgres (DDL est transactionnel), donc
    le rollback final supprime aussi nos partitions — ce qui est exactement
    ce qu'on veut (isolation entre tests).
    """
    # 1. Drop la version create_all (CASCADE car la table parente partitionnée
    #    ne peut pas coexister avec la version standard du même nom).
    await db_session.execute(text('DROP TABLE IF EXISTS "AttendanceRecord" CASCADE'))

    # 2. Recreate partitioned
    await db_session.execute(
        text(
            """
            CREATE TABLE "AttendanceRecord" (
                "id" VARCHAR(30) NOT NULL,
                "personType" "PersonType" NOT NULL,
                "status" "AttendanceStatus" NOT NULL DEFAULT 'PRESENT',
                "scannedAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
                "schoolId" VARCHAR(30) NOT NULL REFERENCES "School"("id"),
                "studentId" VARCHAR(30) REFERENCES "Student"("id"),
                "teacherId" VARCHAR(30) REFERENCES "Teacher"("id"),
                PRIMARY KEY ("id", "scannedAt")
            ) PARTITION BY RANGE ("scannedAt")
            """
        )
    )
    # Indexes héritables (Postgres propage aux partitions enfants).
    await db_session.execute(
        text(
            'CREATE INDEX "ix_AttendanceRecord_schoolId_scannedAt" '
            'ON "AttendanceRecord" ("schoolId", "scannedAt")'
        )
    )
    await db_session.execute(
        text(
            'CREATE INDEX "ix_AttendanceRecord_studentId_scannedAt" '
            'ON "AttendanceRecord" ("studentId", "scannedAt")'
        )
    )

    # 3. Partitions initiales : mois courant + 3 futurs + default.
    today = date.today().replace(day=1)
    cur = today
    for _ in range(4):
        await db_session.execute(text(make_partition_sql(cur.year, cur.month)))
        cur = (
            date(cur.year + 1, 1, 1) if cur.month == 12
            else date(cur.year, cur.month + 1, 1)
        )
    await db_session.execute(
        text(
            'CREATE TABLE "AttendanceRecord_default" '
            'PARTITION OF "AttendanceRecord" DEFAULT'
        )
    )
    await db_session.flush()
    return db_session


async def _make_school(db_session: AsyncSession) -> dict[str, Any]:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    return tree


# ===========================================================================
# 1. PARTITIONNEMENT
# ===========================================================================
@pytest.mark.asyncio
async def test_partition_table_exists_and_is_partitioned(
    attendance_partitioned_table: AsyncSession,
) -> None:
    """``pg_partitioned_table`` connaît bien AttendanceRecord."""
    session = attendance_partitioned_table
    row = await session.execute(
        text(
            """
            SELECT pt.partstrat
            FROM pg_partitioned_table pt
            JOIN pg_class c ON c.oid = pt.partrelid
            WHERE c.relname = 'AttendanceRecord'
            """
        )
    )
    strat = row.scalar_one_or_none()
    # 'r' = RANGE partitioning (cf. pg_partitioned_table docs).
    # asyncpg renvoie le type pg "char" comme bytes — on compare aux deux
    # représentations pour rester pilote-agnostique.
    assert strat in ("r", b"r"), (
        f"AttendanceRecord doit être PARTITION BY RANGE (got {strat!r})"
    )


@pytest.mark.asyncio
async def test_insert_lands_in_correct_partition(
    attendance_partitioned_table: AsyncSession,
) -> None:
    """Une insertion ce mois-ci doit créer une ligne dans la partition
    AttendanceRecord_YYYY_MM (et pas dans _default)."""
    session = attendance_partitioned_table
    tree = await _make_school(session)
    stu = await factories.StudentFactory.create_async(schoolId=tree["school"].id)

    now = datetime.now(UTC)
    rec = AttendanceRecord(
        personType=PersonType.STUDENT,
        status=AttendanceStatus.PRESENT,
        scannedAt=now,
        schoolId=tree["school"].id,
        studentId=stu.id,
    )
    session.add(rec)
    await session.flush()

    expected_partition = partition_name(now.year, now.month)
    row = await session.execute(
        text(f'SELECT COUNT(*) FROM "{expected_partition}"')
    )
    assert row.scalar_one() == 1, (
        f"Le scan aurait dû atterrir dans {expected_partition}"
    )
    # ... et PAS dans la default
    row = await session.execute(
        text('SELECT COUNT(*) FROM "AttendanceRecord_default"')
    )
    assert row.scalar_one() == 0


@pytest.mark.asyncio
async def test_partition_default_catches_out_of_range_date(
    attendance_partitioned_table: AsyncSession,
) -> None:
    """Une date hors range (5 ans dans le passé) doit aller dans _default."""
    session = attendance_partitioned_table
    tree = await _make_school(session)
    stu = await factories.StudentFactory.create_async(schoolId=tree["school"].id)

    way_past = datetime.now(UTC) - timedelta(days=365 * 5)
    rec = AttendanceRecord(
        personType=PersonType.STUDENT,
        status=AttendanceStatus.PRESENT,
        scannedAt=way_past,
        schoolId=tree["school"].id,
        studentId=stu.id,
    )
    session.add(rec)
    await session.flush()

    row = await session.execute(
        text('SELECT COUNT(*) FROM "AttendanceRecord_default"')
    )
    assert row.scalar_one() == 1


@pytest.mark.asyncio
async def test_ensure_future_partitions_creates_missing_and_is_idempotent(
    attendance_partitioned_table: AsyncSession,
) -> None:
    """Premier appel crée des partitions ; second appel = no-op."""
    session = attendance_partitioned_table

    # On demande 6 mois en avance — la fixture en pose 4, donc il en manque 3.
    created_first = await ensure_future_partitions(session, months_ahead=6)
    assert len(created_first) >= 1, "Au moins une partition aurait dû être créée"

    # Second appel : idempotent, aucune création.
    created_second = await ensure_future_partitions(session, months_ahead=6)
    assert created_second == [], (
        f"Idempotence violée : {created_second} créées au second appel"
    )


@pytest.mark.asyncio
async def test_list_partitions_returns_metadata(
    attendance_partitioned_table: AsyncSession,
) -> None:
    """list_partitions renvoie au moins les partitions posées par la fixture."""
    session = attendance_partitioned_table
    rows = await list_partitions(session)
    names = {r["name"] for r in rows}
    today = date.today()
    assert partition_name(today.year, today.month) in names
    assert "AttendanceRecord_default" in names
    for row in rows:
        assert row["rowCount"] >= 0
        assert row["sizeMb"] >= 0.0


# ===========================================================================
# 2. BULK SCAN
# ===========================================================================
@pytest.mark.asyncio
async def test_bulk_scan_inserts_200_records(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    tree = await _make_school(db_session)
    students = await factories.StudentFactory.create_batch_async(
        200, schoolId=tree["school"].id
    )

    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=tree["school"].id
    )
    now = datetime.now(UTC)
    items = [
        {
            "studentId": s.id,
            "status": "PRESENT",
            "scannedAt": now.isoformat(),
        }
        for s in students
    ]
    resp = await client.post(
        "/api/attendance/bulk", json={"items": items}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inserted"] == 200
    assert body["skipped"] == 0
    assert body["errors"] == []
    assert body["by_status"]["PRESENT"] == 200


@pytest.mark.asyncio
async def test_bulk_scan_rejects_future_scannedAt(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    tree = await _make_school(db_session)
    stu = await factories.StudentFactory.create_async(schoolId=tree["school"].id)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=tree["school"].id
    )

    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    resp = await client.post(
        "/api/attendance/bulk",
        json={
            "items": [
                {
                    "studentId": stu.id,
                    "status": "PRESENT",
                    "scannedAt": future,
                }
            ]
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inserted"] == 0
    assert len(body["errors"]) == 1
    assert body["errors"][0]["index"] == 0
    assert "futur" in body["errors"][0]["reason"].lower()


@pytest.mark.asyncio
async def test_bulk_scan_skips_duplicate_same_day(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    tree = await _make_school(db_session)
    stu = await factories.StudentFactory.create_async(schoolId=tree["school"].id)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=tree["school"].id
    )

    # 1er scan via bulk
    now = datetime.now(UTC)
    item = {
        "studentId": stu.id,
        "status": "PRESENT",
        "scannedAt": now.isoformat(),
    }
    resp1 = await client.post(
        "/api/attendance/bulk", json={"items": [item]}, headers=headers
    )
    assert resp1.status_code == 200
    assert resp1.json()["inserted"] == 1

    # 2e bulk identique → skipped (jour déjà couvert)
    resp2 = await client.post(
        "/api/attendance/bulk", json={"items": [item]}, headers=headers
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["inserted"] == 0
    assert body2["skipped"] == 1


@pytest.mark.asyncio
async def test_bulk_scan_partial_failure_returns_errors_array(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    tree = await _make_school(db_session)
    stu_a = await factories.StudentFactory.create_async(schoolId=tree["school"].id)
    stu_b = await factories.StudentFactory.create_async(schoolId=tree["school"].id)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=tree["school"].id
    )

    now = datetime.now(UTC)
    items = [
        {  # ok
            "studentId": stu_a.id,
            "status": "PRESENT",
            "scannedAt": now.isoformat(),
        },
        {  # futur -> error
            "studentId": stu_b.id,
            "status": "PRESENT",
            "scannedAt": (now + timedelta(hours=12)).isoformat(),
        },
        {  # personne inexistante -> error
            "studentId": "x" * 25,
            "status": "PRESENT",
            "scannedAt": now.isoformat(),
        },
    ]
    resp = await client.post(
        "/api/attendance/bulk", json={"items": items}, headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inserted"] == 1
    assert len(body["errors"]) == 2
    indices = sorted(e["index"] for e in body["errors"])
    assert indices == [1, 2]


# ===========================================================================
# 3. STATS
# ===========================================================================
@pytest.mark.asyncio
async def test_attendance_stats_groups_by_day(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    tree = await _make_school(db_session)
    stu = await factories.StudentFactory.create_async(schoolId=tree["school"].id)
    today = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    # 2 jours : 2 présents hier, 1 présent aujourd'hui, 1 absent aujourd'hui
    for scanned_at, status in [
        (yesterday, AttendanceStatus.PRESENT),
        (yesterday.replace(hour=9), AttendanceStatus.PRESENT),
        (today, AttendanceStatus.PRESENT),
        (today.replace(hour=9), AttendanceStatus.ABSENT),
    ]:
        db_session.add(
            AttendanceRecord(
                personType=PersonType.STUDENT,
                status=status,
                scannedAt=scanned_at,
                schoolId=tree["school"].id,
                studentId=stu.id,
            )
        )
    await db_session.flush()

    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=tree["school"].id
    )
    resp = await client.get(
        "/api/attendance/stats",
        params={
            "schoolId": tree["school"].id,
            "dateFrom": yesterday.date().isoformat(),
            "dateTo": today.date().isoformat(),
            "groupBy": "day",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"]["total"] == 4
    assert body["totals"]["present"] == 3
    assert body["totals"]["absent"] == 1
    # Series : 2 buckets jour
    assert len(body["series"]) == 2
    rate = body["attendanceRate"]
    assert 0.74 <= rate <= 0.76  # 3 / 4


@pytest.mark.asyncio
async def test_attendance_stats_filters_by_school(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """Un schoolId explicite restreint l'agrégation à cette école."""
    factories.bind(db_session)
    r = await factories.RegionFactory.create_async()
    s1 = await factories.SchoolFactory.create_async(regionId=r.id)
    s2 = await factories.SchoolFactory.create_async(regionId=r.id)
    stu1 = await factories.StudentFactory.create_async(schoolId=s1.id)
    stu2 = await factories.StudentFactory.create_async(schoolId=s2.id)

    now = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0)
    for stu, sid in ((stu1, s1.id), (stu1, s1.id), (stu2, s2.id)):
        db_session.add(
            AttendanceRecord(
                personType=PersonType.STUDENT,
                status=AttendanceStatus.PRESENT,
                scannedAt=now,
                schoolId=sid,
                studentId=stu.id,
            )
        )
    await db_session.flush()

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get(
        "/api/attendance/stats",
        params={
            "schoolId": s1.id,
            "dateFrom": now.date().isoformat(),
            "dateTo": now.date().isoformat(),
            "groupBy": "day",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["total"] == 2, body
    assert body["totals"]["present"] == 2


@pytest.mark.asyncio
async def test_attendance_stats_caches_in_redis(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
    redis_client: Redis,
) -> None:
    tree = await _make_school(db_session)
    stu = await factories.StudentFactory.create_async(schoolId=tree["school"].id)
    now = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0)
    db_session.add(
        AttendanceRecord(
            personType=PersonType.STUDENT,
            status=AttendanceStatus.PRESENT,
            scannedAt=now,
            schoolId=tree["school"].id,
            studentId=stu.id,
        )
    )
    await db_session.flush()

    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=tree["school"].id
    )
    # Sanity : aucune clé attendance:stats: avant l'appel
    before = await redis_client.keys(f"{STATS_CACHE_PREFIX}*")
    assert before == []

    resp = await client.get(
        "/api/attendance/stats",
        params={
            "schoolId": tree["school"].id,
            "dateFrom": now.date().isoformat(),
            "dateTo": now.date().isoformat(),
            "groupBy": "day",
        },
        headers=headers,
    )
    assert resp.status_code == 200

    after = await redis_client.keys(f"{STATS_CACHE_PREFIX}*")
    assert len(after) == 1, "Une clé de cache aurait dû être créée"
    # TTL ~ 60s (on accepte un peu de slop)
    ttl = await redis_client.ttl(after[0])
    assert 0 < ttl <= 60


@pytest.mark.asyncio
async def test_attendance_stats_respects_scope(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """Un directeur d'une école ne peut pas demander les stats d'une autre."""
    factories.bind(db_session)
    r = await factories.RegionFactory.create_async()
    s1 = await factories.SchoolFactory.create_async(regionId=r.id)
    s2 = await factories.SchoolFactory.create_async(regionId=r.id)
    stu2 = await factories.StudentFactory.create_async(schoolId=s2.id)

    now = datetime.now(UTC).replace(hour=8, minute=0, second=0, microsecond=0)
    db_session.add(
        AttendanceRecord(
            personType=PersonType.STUDENT,
            status=AttendanceStatus.PRESENT,
            scannedAt=now,
            schoolId=s2.id,
            studentId=stu2.id,
        )
    )
    await db_session.flush()

    # Directeur de s1 demande les stats de s2 → scope filtre vide → 0 lignes.
    headers = await auth_headers(UserRole.SCHOOL_DIRECTOR, schoolId=s1.id)
    resp = await client.get(
        "/api/attendance/stats",
        params={
            "schoolId": s2.id,
            "dateFrom": now.date().isoformat(),
            "dateTo": now.date().isoformat(),
            "groupBy": "day",
        },
        headers=headers,
    )
    # Le service renvoie une réponse vide (pas un 403, car la résolution
    # passe par scoped_schools=[] -> early return).
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["total"] == 0
    assert body["series"] == []


# ===========================================================================
# 4. RBAC sur les nouveaux endpoints
# ===========================================================================
@pytest.mark.asyncio
async def test_partitions_endpoint_requires_national_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """Un directeur ne peut PAS lister les partitions."""
    tree = await _make_school(db_session)
    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=tree["school"].id
    )
    resp = await client.get("/api/attendance/partitions", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_partitions_endpoint_accepts_national_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    resp = await client.get("/api/attendance/partitions", headers=headers)
    # On peut tomber sur 200 (vide ou non) — l'important c'est PAS un 403.
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_bulk_scan_requires_director_role(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers: Any,
) -> None:
    """Un TEACHER ne peut PAS appeler /bulk (anti-saisie masse)."""
    tree = await _make_school(db_session)
    headers = await auth_headers(
        UserRole.TEACHER, schoolId=tree["school"].id
    )
    resp = await client.post(
        "/api/attendance/bulk",
        json={"items": []},
        headers=headers,
    )
    assert resp.status_code == 403


# ===========================================================================
# 5. Schemas & helpers (validation Pydantic — pas besoin de DB)
# ===========================================================================
def test_bulk_scan_item_requires_exactly_one_person() -> None:
    """BulkScanItem doit avoir EXACTEMENT un de studentId/teacherId."""
    from app.modules.attendance.schemas import BulkScanItem

    # Aucun
    with pytest.raises(Exception):
        BulkScanItem(status=AttendanceStatus.PRESENT, scannedAt=datetime.now(UTC))
    # Les deux
    with pytest.raises(Exception):
        BulkScanItem(
            studentId="x" * 25,
            teacherId="y" * 25,
            status=AttendanceStatus.PRESENT,
            scannedAt=datetime.now(UTC),
        )
    # OK
    item = BulkScanItem(
        studentId="x" * 25,
        status=AttendanceStatus.PRESENT,
        scannedAt=datetime.now(UTC),
    )
    assert item.studentId is not None


def test_attendance_stats_filter_rejects_period_too_long() -> None:
    from app.modules.attendance.schemas import AttendanceStatsFilter

    with pytest.raises(Exception):
        AttendanceStatsFilter(
            schoolId="x" * 25,
            dateFrom=date(2024, 1, 1),
            dateTo=date(2026, 1, 2),  # 2 ans > 366j
            groupBy="day",
        )


def test_attendance_stats_filter_requires_a_target() -> None:
    from app.modules.attendance.schemas import AttendanceStatsFilter

    with pytest.raises(Exception):
        AttendanceStatsFilter(
            dateFrom=date(2026, 1, 1),
            dateTo=date(2026, 1, 31),
            groupBy="day",
        )


def test_make_partition_sql_format() -> None:
    sql = make_partition_sql(2026, 5)
    assert "AttendanceRecord_2026_05" in sql
    assert "'2026-05-01'" in sql
    assert "'2026-06-01'" in sql
    assert sql.startswith("CREATE TABLE IF NOT EXISTS")
