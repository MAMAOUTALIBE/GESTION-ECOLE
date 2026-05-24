"""Module 18 — Portail parent (WhatsApp + USSD enrichi + page publique).

Couvre :

1.  Intent parser : MOYENNE reconnu.
2.  Intent parser : PRESENCE reconnu (avec/sans accent).
3.  Intent parser : inconnu → fallback AIDE.
4.  Intent parser : case-insensitive ET accent-insensitive.
5.  handle_whatsapp_message : parent connu → réponse contient moyenne.
6.  handle_whatsapp_message : phone inconnu → message d'aide générique.
7.  handle_whatsapp_message : crée une ParentSession + log WhatsAppMessage.
8.  Webhook : HMAC invalide → 401.
9.  Webhook : HMAC valide → 200.
10. GET /overview : payload anonymisé (initiales seulement).
11. GET /overview : 21e requête rate-limit 429.
12. GET /parent : HTML rendu avec initiales + classe + moyenne, sans nom complet.
13. Session expirée après 30 minutes (find_active_session retourne None).
14. Parent multi-enfants : la réponse liste tous les enfants.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import (
    AcademicPeriod,
    ReportCard,
    SchoolYear,
)
from app.modules.parent_portal.enums import (
    ParentChannel,
    ParentIntent,
    WhatsAppDirection,
)
from app.modules.parent_portal.intent_parser import parse_intent
from app.modules.parent_portal.models import ParentSession, WhatsAppMessage
from app.modules.parent_portal.service import (
    ParentPortalService,
    hash_phone,
)
from app.shared.base import generate_cuid
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def _no_hmac_secret() -> Any:
    """Garantit que WHATSAPP_HMAC_SECRET est vide pour les tests qui ne le testent pas."""
    previous = os.environ.pop("WHATSAPP_HMAC_SECRET", None)
    yield
    if previous is not None:
        os.environ["WHATSAPP_HMAC_SECRET"] = previous


@pytest_asyncio.fixture(loop_scope="session")
async def parent_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Crée un élève avec un guardianPhone normalisé + bulletin."""
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()

    student = await factories.StudentFactory.create_async(
        schoolId=tree["school"].id,
        firstName="Aissatou",
        lastName="Diallo",
        guardianPhone="+224622112233",
        guardianName="Mama Diallo",
    )

    # Bulletin
    from app.shared.enums import AcademicPeriodType
    school_year = SchoolYear(
        id=generate_cuid(),
        name="2025-2026",
        startDate=datetime(2025, 9, 1, tzinfo=UTC),
        endDate=datetime(2026, 7, 31, tzinfo=UTC),
        periodType=AcademicPeriodType.TRIMESTER,
        isActive=True,
    )
    db_session.add(school_year)
    await db_session.flush()
    period = AcademicPeriod(
        id=generate_cuid(),
        schoolYearId=school_year.id,
        name="Trimestre 1",
        order=1,
        type=AcademicPeriodType.TRIMESTER,
        startDate=datetime(2025, 9, 1, tzinfo=UTC),
        endDate=datetime(2025, 12, 15, tzinfo=UTC),
    )
    db_session.add(period)
    await db_session.flush()

    report = ReportCard(
        id=generate_cuid(),
        studentId=student.id,
        schoolYearId=school_year.id,
        periodId=period.id,
        average=15.50,
        rank=2,
        totalStudents=40,
        verificationCode=f"RC-{generate_cuid()[:12]}",
    )
    db_session.add(report)
    await db_session.flush()

    return {
        "tree": tree,
        "student": student,
        "school_year": school_year,
        "period": period,
        "report": report,
    }


# ===========================================================================
# 1. intent_parser reconnaît MOYENNE
# ===========================================================================
def test_intent_parser_recognizes_moyenne() -> None:
    assert parse_intent("moyenne") == ParentIntent.MOYENNE
    assert parse_intent("Quelle est ma moyenne ?") == ParentIntent.MOYENNE
    assert parse_intent("note") == ParentIntent.MOYENNE


# ===========================================================================
# 2. intent_parser reconnaît PRESENCE (avec/sans accent)
# ===========================================================================
def test_intent_parser_recognizes_presence() -> None:
    assert parse_intent("presence") == ParentIntent.PRESENCE
    assert parse_intent("Présence svp") == ParentIntent.PRESENCE
    assert parse_intent("absences") == ParentIntent.PRESENCE
    assert parse_intent("retard") == ParentIntent.PRESENCE


# ===========================================================================
# 3. intent_parser inconnu → AIDE
# ===========================================================================
def test_intent_parser_unknown_falls_back_to_aide() -> None:
    assert parse_intent("xyzabc random text") == ParentIntent.AIDE
    assert parse_intent("") == ParentIntent.AIDE
    assert parse_intent("hello") == ParentIntent.AIDE


# ===========================================================================
# 4. intent_parser case-insensitive ET accent-insensitive
# ===========================================================================
def test_intent_parser_case_insensitive_and_accents() -> None:
    # variantes de casse
    assert parse_intent("MOYENNE") == ParentIntent.MOYENNE
    assert parse_intent("MoYeNnE") == ParentIntent.MOYENNE
    # accents : "Présence" / "présence" / "presence"
    assert parse_intent("Présence") == ParentIntent.PRESENCE
    assert parse_intent("présence") == ParentIntent.PRESENCE
    assert parse_intent("ÉVÉNEMENT") == ParentIntent.EVENEMENT


# ===========================================================================
# 5. handle_whatsapp_message : parent connu → réponse contient moyenne
# ===========================================================================
@pytest.mark.asyncio
async def test_handle_whatsapp_message_known_parent_returns_overview(
    db_session: AsyncSession, parent_ctx: dict[str, Any],
) -> None:
    service = ParentPortalService(db_session)
    reply = await service.handle_whatsapp_message(
        phone_number="+224622112233",
        body="moyenne",
        message_id="wamid.test-1",
    )
    assert reply.intent == ParentIntent.MOYENNE.value
    assert "Aissatou" in reply.reply
    assert "15.50" in reply.reply


# ===========================================================================
# 6. handle_whatsapp_message : numéro inconnu → message d'aide
# ===========================================================================
@pytest.mark.asyncio
async def test_handle_whatsapp_message_unknown_phone_returns_help(
    db_session: AsyncSession,
) -> None:
    factories.bind(db_session)
    service = ParentPortalService(db_session)
    reply = await service.handle_whatsapp_message(
        phone_number="+224622999777",
        body="moyenne",
        message_id="wamid.unknown-1",
    )
    # Le numéro est inconnu : on ne révèle pas la liste des intents,
    # on invite à contacter l'école.
    assert "non reconnu" in reply.reply.lower() or "contactez" in reply.reply.lower()


# ===========================================================================
# 7. handle_whatsapp_message crée une ParentSession + logs WhatsAppMessage
# ===========================================================================
@pytest.mark.asyncio
async def test_handle_whatsapp_creates_session_and_logs(
    db_session: AsyncSession, parent_ctx: dict[str, Any],
) -> None:
    service = ParentPortalService(db_session)
    await service.handle_whatsapp_message(
        phone_number="+224622112233",
        body="moyenne",
        message_id="wamid.session-1",
    )

    # 1 ParentSession WHATSAPP créée
    phone_hash = hash_phone("+224622112233")
    sessions = (
        await db_session.execute(
            select(ParentSession)
            .where(ParentSession.phoneNumberHash == phone_hash)
            .where(ParentSession.channel == ParentChannel.WHATSAPP)
        )
    ).scalars().all()
    assert len(list(sessions)) == 1

    # 2 WhatsAppMessages : 1 INBOUND, 1 OUTBOUND
    rows = (
        await db_session.execute(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.phoneNumber == "+224622112233")
        )
    ).scalars().all()
    rows_list = list(rows)
    assert len(rows_list) == 2
    directions = {r.direction for r in rows_list}
    assert WhatsAppDirection.INBOUND in directions
    assert WhatsAppDirection.OUTBOUND in directions


# ===========================================================================
# 8. webhook : HMAC invalide → 401
# ===========================================================================
@pytest.mark.asyncio
async def test_whatsapp_webhook_rejects_invalid_hmac(
    client: AsyncClient,
) -> None:
    secret = "module18-hmac-secret"
    os.environ["WHATSAPP_HMAC_SECRET"] = secret
    try:
        payload = {
            "phoneNumber": "+224622500500",
            "body": "moyenne",
            "messageId": "wamid.hmac-bad",
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        # Sans signature
        r_missing = await client.post(
            "/api/parent-portal/whatsapp/webhook",
            content=body_bytes,
            headers={"content-type": "application/json"},
        )
        assert r_missing.status_code == 401, r_missing.text

        # Signature fausse
        r_bad = await client.post(
            "/api/parent-portal/whatsapp/webhook",
            content=body_bytes,
            headers={
                "content-type": "application/json",
                "X-WhatsApp-Signature": "deadbeef" * 8,
            },
        )
        assert r_bad.status_code == 401, r_bad.text
    finally:
        os.environ.pop("WHATSAPP_HMAC_SECRET", None)


# ===========================================================================
# 9. webhook : HMAC valide → 200
# ===========================================================================
@pytest.mark.asyncio
async def test_whatsapp_webhook_accepts_valid_hmac(
    client: AsyncClient,
) -> None:
    secret = "module18-hmac-secret"
    os.environ["WHATSAPP_HMAC_SECRET"] = secret
    try:
        payload = {
            "phoneNumber": "+224622500501",
            "body": "aide",
            "messageId": "wamid.hmac-ok",
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        good_sig = hmac.new(
            secret.encode("utf-8"), body_bytes, hashlib.sha256
        ).hexdigest()
        r = await client.post(
            "/api/parent-portal/whatsapp/webhook",
            content=body_bytes,
            headers={
                "content-type": "application/json",
                "X-WhatsApp-Signature": good_sig,
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert "intent" in data and "reply" in data
    finally:
        os.environ.pop("WHATSAPP_HMAC_SECRET", None)


# ===========================================================================
# 10. GET /overview/{hash} : payload anonymisé (initiales seulement)
# ===========================================================================
@pytest.mark.asyncio
async def test_get_overview_returns_anonymized_data(
    client: AsyncClient, parent_ctx: dict[str, Any], _no_hmac_secret,
) -> None:
    phone_hash = hash_phone("+224622112233")
    r = await client.get(f"/api/parent-portal/overview/{phone_hash}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["phoneHash"] == phone_hash
    assert data["childrenCount"] == 1
    child = data["children"][0]
    # Anonymisé : initiales seulement, PAS de nom complet
    assert child["initials"] == "A.D."
    assert "Aissatou" not in json.dumps(data)
    assert "Diallo" not in json.dumps(data)
    assert child["lastAverage"] == 15.50


# ===========================================================================
# 11. /overview endpoint respecte le rate-limit 20/min/hash
# ===========================================================================
@pytest.mark.asyncio
async def test_overview_endpoint_respects_rate_limit(
    client: AsyncClient, _no_hmac_secret,
) -> None:
    fake_hash = "a" * 64
    # 20 premières doivent passer
    for i in range(20):
        r = await client.get(f"/api/parent-portal/overview/{fake_hash}")
        assert r.status_code == 200, f"req #{i + 1} should pass: {r.text}"
    # 21e doit être 429
    r = await client.get(f"/api/parent-portal/overview/{fake_hash}")
    assert r.status_code == 429, r.text


# ===========================================================================
# 12. /parent/{hash} HTML — initiales, pas de nom complet
# ===========================================================================
@pytest.mark.asyncio
async def test_html_parent_page_renders_with_student_initials_only(
    client: AsyncClient, parent_ctx: dict[str, Any], _no_hmac_secret,
) -> None:
    phone_hash = hash_phone("+224622112233")
    r = await client.get(f"/api/parent-portal/parent/{phone_hash}")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers.get("content-type", "")
    html = r.text
    # Présence des initiales
    assert "A.D." in html
    # Présence de la moyenne formatée
    assert "15.50" in html
    # PAS de nom complet visible
    assert "Aissatou" not in html
    assert "Diallo" not in html


# ===========================================================================
# 13. Session expire après 30 minutes
# ===========================================================================
@pytest.mark.asyncio
async def test_session_expires_after_30_minutes(
    db_session: AsyncSession,
) -> None:
    factories.bind(db_session)
    service = ParentPortalService(db_session)
    phone = "+224622800800"

    # Crée une session manuellement avec expiresAt dans le passé
    phone_hash = hash_phone(phone)
    past = datetime.now(UTC) - timedelta(minutes=31)
    stale_session = ParentSession(
        id=generate_cuid(),
        phoneNumberHash=phone_hash,
        channel=ParentChannel.WHATSAPP,
        startedAt=past,
        lastActivityAt=past,
        expiresAt=past + timedelta(minutes=30),  # déjà expirée
    )
    db_session.add(stale_session)
    await db_session.flush()

    active = await service.find_active_session(
        phone=phone, channel=ParentChannel.WHATSAPP,
    )
    assert active is None


# ===========================================================================
# 14. Parent avec plusieurs enfants : tous listés dans la réponse
# ===========================================================================
@pytest.mark.asyncio
async def test_parent_with_multiple_children_listed(
    db_session: AsyncSession,
) -> None:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()

    parent_phone = "+224622700700"
    await factories.StudentFactory.create_async(
        schoolId=tree["school"].id,
        firstName="Mariam",
        lastName="Bah",
        guardianPhone=parent_phone,
        guardianName="Père Bah",
    )
    await factories.StudentFactory.create_async(
        schoolId=tree["school"].id,
        firstName="Ousmane",
        lastName="Bah",
        guardianPhone=parent_phone,
        guardianName="Père Bah",
    )

    service = ParentPortalService(db_session)
    overview = await service.get_parent_overview(hash_phone(parent_phone))
    assert overview.childrenCount == 2
    initials = {c.initials for c in overview.children}
    assert {"M.B.", "O.B."} == initials

    # Réponse WhatsApp doit aussi lister les deux
    reply = await service.handle_whatsapp_message(
        phone_number=parent_phone,
        body="moyenne",
        message_id="wamid.multi-1",
    )
    assert "Mariam" in reply.reply
    assert "Ousmane" in reply.reply
