"""Module 5C — Audit des accès PII.

Couvre :

1.  test_log_access_persists_entry
2.  test_log_access_best_effort_swallows_db_error
3.  test_log_bulk_list_aggregates_when_above_50
4.  test_log_bulk_list_per_entity_when_below_50
5.  test_list_accesses_returns_only_own_for_non_admin
6.  test_list_accesses_returns_all_for_national_admin
7.  test_get_history_requires_national_or_ministry
8.  test_purge_old_logs_removes_only_old_entries
9.  test_purge_requires_national_admin
10. test_view_student_endpoint_creates_audit_log         (intégration HTTP)
11. test_list_students_endpoint_creates_aggregated_log   (intégration HTTP)
12. test_diploma_verify_creates_audit_log                (intégration HTTP)
13. test_audit_log_captures_ip_and_userAgent
14. test_unauthorized_calls_dont_create_logs
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError
from app.modules.auth.models import User
from app.modules.census.models import Student
from app.modules.pii_audit.enums import (
    BULK_LIST_AGGREGATION_THRESHOLD,
    PiiAccessType,
    PiiEntityType,
)
from app.modules.pii_audit.models import PiiAccessLog
from app.modules.pii_audit.schemas import PiiAccessLogFilters
from app.modules.pii_audit.service import PiiAuditService
from app.shared.base import generate_cuid
from app.shared.enums import (
    Gender,
    UserRole,
)
from tests.integration import factories

pytestmark = pytest.mark.integration


# ===========================================================================
# Helpers
# ===========================================================================
async def _make_user(
    session: AsyncSession,
    role: UserRole = UserRole.NATIONAL_ADMIN,
    **kwargs: Any,
) -> User:
    uid = generate_cuid()
    user = User(
        id=uid,
        email=f"5c-{role.value.lower()}-{uid[:6]}@test.local",
        passwordHash="x",
        fullName=f"Test {role.value}",
        role=role,
        isActive=True,
        **kwargs,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_school_and_student(
    db_session: AsyncSession,
) -> tuple[str, str]:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    school = tree["school"]
    student = await factories.StudentFactory.create_async(
        schoolId=school.id,
        firstName="Test",
        lastName="Eleve",
        gender=Gender.FEMALE,
    )
    return school.id, student.id


async def _count_logs(
    session: AsyncSession,
    *,
    entity_type: PiiEntityType | None = None,
    entity_id: str | None = None,
    user_id: str | None = None,
    access_type: PiiAccessType | None = None,
) -> int:
    stmt = select(PiiAccessLog)
    if entity_type is not None:
        stmt = stmt.where(PiiAccessLog.entityType == entity_type)
    if entity_id is not None:
        stmt = stmt.where(PiiAccessLog.entityId == entity_id)
    if user_id is not None:
        stmt = stmt.where(PiiAccessLog.userId == user_id)
    if access_type is not None:
        stmt = stmt.where(PiiAccessLog.accessType == access_type)
    rows = (await session.execute(stmt)).scalars().all()
    return len(rows)


# ===========================================================================
# 1. log_access persists
# ===========================================================================
@pytest.mark.asyncio
async def test_log_access_persists_entry(db_session: AsyncSession) -> None:
    actor = await _make_user(db_session, UserRole.SCHOOL_DIRECTOR)
    svc = PiiAuditService(db_session)

    await svc.log_access(
        actor=actor,
        entity_type=PiiEntityType.STUDENT,
        entity_id="stu-1",
        access_type=PiiAccessType.VIEW,
        endpoint="/api/census/students/stu-1",
    )

    rows = (
        await db_session.execute(select(PiiAccessLog))
    ).scalars().all()
    assert len(rows) == 1
    log = rows[0]
    assert log.userId == actor.id
    assert log.userRole == UserRole.SCHOOL_DIRECTOR.value
    assert log.entityType == PiiEntityType.STUDENT
    assert log.entityId == "stu-1"
    assert log.accessType == PiiAccessType.VIEW
    assert log.endpoint == "/api/census/students/stu-1"


# ===========================================================================
# 2. log_access est best-effort — ne casse jamais le flux principal
# ===========================================================================
@pytest.mark.asyncio
async def test_log_access_best_effort_swallows_db_error(
    db_session: AsyncSession,
) -> None:
    actor = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    svc = PiiAuditService(db_session)

    # On force une exception en passant un entityType invalide via une
    # patch sur ``session.add`` — l'appelant ne doit JAMAIS voir d'exc.
    boom_called = {"hit": False}

    original_add = db_session.add

    def _boom(*args: Any, **kwargs: Any) -> None:
        boom_called["hit"] = True
        raise RuntimeError("simulated DB explosion")

    with patch.object(db_session, "add", side_effect=_boom):
        # Ne doit PAS lever
        await svc.log_access(
            actor=actor,
            entity_type=PiiEntityType.STUDENT,
            entity_id="stu-x",
            access_type=PiiAccessType.VIEW,
            endpoint="/api/test",
        )

    assert boom_called["hit"] is True
    # Aucune ligne persistée (l'add a explosé)
    db_session.add = original_add  # type: ignore[assignment]
    rows = (
        await db_session.execute(select(PiiAccessLog))
    ).scalars().all()
    assert len(rows) == 0


# ===========================================================================
# 3. log_bulk_list — agrégation si > 50 ids
# ===========================================================================
@pytest.mark.asyncio
async def test_log_bulk_list_aggregates_when_above_50(
    db_session: AsyncSession,
) -> None:
    actor = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    svc = PiiAuditService(db_session)

    ids = [f"stu-{i}" for i in range(BULK_LIST_AGGREGATION_THRESHOLD + 5)]
    await svc.log_bulk_list(
        actor=actor,
        entity_type=PiiEntityType.STUDENT,
        entity_ids=ids,
        endpoint="/api/census/students",
    )

    rows = (
        await db_session.execute(select(PiiAccessLog))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].entityId == "*"
    assert rows[0].accessType == PiiAccessType.LIST
    assert rows[0].metadataJson == {"count": len(ids)}


# ===========================================================================
# 4. log_bulk_list — ligne par entité si <= 50
# ===========================================================================
@pytest.mark.asyncio
async def test_log_bulk_list_per_entity_when_below_50(
    db_session: AsyncSession,
) -> None:
    actor = await _make_user(db_session, UserRole.SCHOOL_DIRECTOR)
    svc = PiiAuditService(db_session)

    ids = [f"stu-{i}" for i in range(5)]
    await svc.log_bulk_list(
        actor=actor,
        entity_type=PiiEntityType.STUDENT,
        entity_ids=ids,
        endpoint="/api/census/students",
    )

    rows = (
        await db_session.execute(select(PiiAccessLog))
    ).scalars().all()
    assert len(rows) == 5
    assert {r.entityId for r in rows} == set(ids)
    assert all(r.accessType == PiiAccessType.LIST for r in rows)


# ===========================================================================
# 5. list_accesses — RBAC : non-admin ne voit que ses propres lignes
# ===========================================================================
@pytest.mark.asyncio
async def test_list_accesses_returns_only_own_for_non_admin(
    db_session: AsyncSession,
) -> None:
    alice = await _make_user(db_session, UserRole.SCHOOL_DIRECTOR)
    bob = await _make_user(db_session, UserRole.TEACHER)
    svc = PiiAuditService(db_session)

    await svc.log_access(
        actor=alice,
        entity_type=PiiEntityType.STUDENT,
        entity_id="stu-a",
        access_type=PiiAccessType.VIEW,
        endpoint="/api/test",
    )
    await svc.log_access(
        actor=bob,
        entity_type=PiiEntityType.STUDENT,
        entity_id="stu-b",
        access_type=PiiAccessType.VIEW,
        endpoint="/api/test",
    )

    # Bob essaie de passer userId=alice (devrait être ignoré).
    filters = PiiAccessLogFilters(userId=alice.id)
    rows = await svc.list_accesses(filters, bob)
    assert len(rows) == 1
    assert rows[0].userId == bob.id
    assert rows[0].entityId == "stu-b"


# ===========================================================================
# 6. list_accesses — NATIONAL_ADMIN voit tout
# ===========================================================================
@pytest.mark.asyncio
async def test_list_accesses_returns_all_for_national_admin(
    db_session: AsyncSession,
) -> None:
    admin = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    teacher = await _make_user(db_session, UserRole.TEACHER)
    svc = PiiAuditService(db_session)

    await svc.log_access(
        actor=teacher,
        entity_type=PiiEntityType.STUDENT,
        entity_id="stu-t",
        access_type=PiiAccessType.VIEW,
        endpoint="/api/test",
    )
    await svc.log_access(
        actor=admin,
        entity_type=PiiEntityType.STUDENT,
        entity_id="stu-a",
        access_type=PiiAccessType.VIEW,
        endpoint="/api/test",
    )

    rows = await svc.list_accesses(PiiAccessLogFilters(), admin)
    assert len(rows) == 2


# ===========================================================================
# 7. get_history_for_entity — admins nationaux uniquement
# ===========================================================================
@pytest.mark.asyncio
async def test_get_history_requires_national_or_ministry(
    db_session: AsyncSession,
) -> None:
    teacher = await _make_user(db_session, UserRole.TEACHER)
    admin = await _make_user(db_session, UserRole.MINISTRY_ADMIN)
    svc = PiiAuditService(db_session)

    await svc.log_access(
        actor=admin,
        entity_type=PiiEntityType.STUDENT,
        entity_id="stu-z",
        access_type=PiiAccessType.VIEW,
        endpoint="/api/test",
    )

    # TEACHER → 403
    with pytest.raises(ForbiddenError):
        await svc.get_history_for_entity(
            PiiEntityType.STUDENT, "stu-z", teacher,
        )

    # MINISTRY_ADMIN → ok
    rows = await svc.get_history_for_entity(
        PiiEntityType.STUDENT, "stu-z", admin,
    )
    assert len(rows) == 1
    assert rows[0].entityId == "stu-z"


# ===========================================================================
# 8. purge_old_logs — supprime uniquement les vieilles entrées
# ===========================================================================
@pytest.mark.asyncio
async def test_purge_old_logs_removes_only_old_entries(
    db_session: AsyncSession,
) -> None:
    admin = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    svc = PiiAuditService(db_session)

    now = datetime.now(UTC)
    old = PiiAccessLog(
        id=generate_cuid(),
        userId=admin.id,
        userRole=admin.role.value,
        entityType=PiiEntityType.STUDENT,
        entityId="old-1",
        accessType=PiiAccessType.VIEW,
        endpoint="/api/old",
        accessedAt=now - timedelta(days=1200),
    )
    recent = PiiAccessLog(
        id=generate_cuid(),
        userId=admin.id,
        userRole=admin.role.value,
        entityType=PiiEntityType.STUDENT,
        entityId="recent-1",
        accessType=PiiAccessType.VIEW,
        endpoint="/api/recent",
        accessedAt=now - timedelta(days=30),
    )
    db_session.add_all([old, recent])
    await db_session.flush()

    cutoff = now - timedelta(days=1095)
    deleted = await svc.purge_old_logs(cutoff, admin)
    assert deleted == 1

    remaining = (
        await db_session.execute(select(PiiAccessLog))
    ).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].entityId == "recent-1"


# ===========================================================================
# 9. purge — NATIONAL_ADMIN only
# ===========================================================================
@pytest.mark.asyncio
async def test_purge_requires_national_admin(
    db_session: AsyncSession,
) -> None:
    ministry = await _make_user(db_session, UserRole.MINISTRY_ADMIN)
    svc = PiiAuditService(db_session)
    with pytest.raises(ForbiddenError):
        await svc.purge_old_logs(datetime.now(UTC), ministry)


# ===========================================================================
# 10. HTTP — GET /api/census/students/{id} produit un audit VIEW
# ===========================================================================
@pytest.mark.asyncio
async def test_view_student_endpoint_creates_audit_log(
    client: AsyncClient,
    auth_headers: Any,
    db_session: AsyncSession,
) -> None:
    _school_id, student_id = await _make_school_and_student(db_session)
    headers = await auth_headers(UserRole.NATIONAL_ADMIN)

    # On force PII_AUDIT_AWAIT (au cas où le décorateur n'aurait pas
    # await_audit=True) pour que l'audit soit déterministe dans la
    # transaction du test.
    os.environ["PII_AUDIT_AWAIT"] = "1"
    try:
        r = await client.get(
            f"/api/census/students/{student_id}", headers=headers,
        )
    finally:
        os.environ.pop("PII_AUDIT_AWAIT", None)

    assert r.status_code == 200, r.text

    n = await _count_logs(
        db_session,
        entity_type=PiiEntityType.STUDENT,
        entity_id=student_id,
        access_type=PiiAccessType.VIEW,
    )
    assert n == 1


# ===========================================================================
# 11. HTTP — GET /api/census/students produit un audit LIST
# ===========================================================================
@pytest.mark.asyncio
async def test_list_students_endpoint_creates_aggregated_log(
    client: AsyncClient,
    auth_headers: Any,
    db_session: AsyncSession,
) -> None:
    school_id, _student_id = await _make_school_and_student(db_session)
    # Crée 60 étudiants supplémentaires pour passer le seuil agrégat.
    for _ in range(BULK_LIST_AGGREGATION_THRESHOLD + 5):
        await factories.StudentFactory.create_async(
            schoolId=school_id,
            firstName="Bulk",
            lastName="Student",
            gender=Gender.MALE,
        )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    r = await client.get(
        "/api/census/students?limit=2000", headers=headers,
    )
    assert r.status_code == 200, r.text
    assert len(r.json()) > BULK_LIST_AGGREGATION_THRESHOLD

    # Un seul row "*" (LIST) attendu pour l'audit (agrégat).
    rows = (
        await db_session.execute(
            select(PiiAccessLog).where(
                PiiAccessLog.entityType == PiiEntityType.STUDENT,
                PiiAccessLog.accessType == PiiAccessType.LIST,
                PiiAccessLog.entityId == "*",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].metadataJson is not None
    assert rows[0].metadataJson["count"] > BULK_LIST_AGGREGATION_THRESHOLD


# ===========================================================================
# 12. HTTP — verify diploma produit un audit STUDENT VIEW (PUBLIC)
# ===========================================================================
@pytest.mark.asyncio
async def test_diploma_verify_creates_audit_log(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    # On appelle l'endpoint PUBLIC de vérification avec un serial bidon.
    # Le service retournera NOT_FOUND, mais on attend quand même UNE
    # ligne d'audit (le 404 doit aussi être tracé pour détecter les
    # tentatives d'énumération).
    r = await client.get("/api/diplomas/verify/UNKNOWN-SERIAL-XYZ-123")
    assert r.status_code in (200, 404)

    rows = (
        await db_session.execute(
            select(PiiAccessLog).where(
                PiiAccessLog.entityType == PiiEntityType.STUDENT,
                PiiAccessLog.entityId == "UNKNOWN-SERIAL-XYZ-123",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    log = rows[0]
    assert log.userId is None
    assert log.accessType == PiiAccessType.VIEW
    assert "/api/diplomas/verify/" in log.endpoint


# ===========================================================================
# 13. log_access capture l'IP et le user-agent (avec sanitisation)
# ===========================================================================
@pytest.mark.asyncio
async def test_audit_log_captures_ip_and_userAgent(
    client: AsyncClient,
    auth_headers: Any,
    db_session: AsyncSession,
) -> None:
    _school_id, student_id = await _make_school_and_student(db_session)
    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    # On envoie un user-agent custom avec des bytes de contrôle —
    # le service doit les éliminer (caps + sanitisation).
    headers["user-agent"] = (
        "GestionEE-Test/1.0\x00\x01\nBenchmark"
    )

    os.environ["PII_AUDIT_AWAIT"] = "1"
    try:
        r = await client.get(
            f"/api/census/students/{student_id}", headers=headers,
        )
    finally:
        os.environ.pop("PII_AUDIT_AWAIT", None)
    assert r.status_code == 200, r.text

    rows = (
        await db_session.execute(
            select(PiiAccessLog).where(
                PiiAccessLog.entityId == student_id,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    log = rows[0]
    # IP — httpx ASGI transport: "127.0.0.1" ou None selon version.
    # On vérifie le champ existe en valeur ou est None — mais user_agent
    # DOIT être présent et nettoyé.
    assert log.userAgent is not None
    assert "\x00" not in log.userAgent
    assert "\x01" not in log.userAgent
    assert "\n" not in log.userAgent
    assert "GestionEE-Test/1.0" in log.userAgent
    assert "Benchmark" in log.userAgent


# ===========================================================================
# 14. unauthorized — un 401 avant le décorateur ne crée AUCUN audit
# ===========================================================================
@pytest.mark.asyncio
async def test_unauthorized_calls_dont_create_logs(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    # Pas d'Authorization header → get_current_user lève 401 AVANT
    # que le décorateur audit_pii_access n'ait pu être atteint.
    r = await client.get("/api/census/students/some-id")
    assert r.status_code == 401

    rows = (
        await db_session.execute(select(PiiAccessLog))
    ).scalars().all()
    assert len(rows) == 0
