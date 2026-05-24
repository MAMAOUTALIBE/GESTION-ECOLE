"""Module 14 — SMS / USSD gateway.

Couvre :

1. Provider abstraction : MockProvider persiste un SmsMessage SENT.
2. RBAC sur ``POST /api/sms/send``.
3. ``send_templated`` utilise la langue du destinataire (fr vs ff).
4. Menu USSD d'accueil sur texte vide.
5. Option 1 (moyenne) : élève connu via guardianPhone → moyenne.
6. Option 1 sur numéro sans student → message d'aide.
7. Option 3 (diplôme) : résolution par code élève → status VALID.
8. Persistance + reprise de session (sessionId stable).
9. Rate limit USSD : 5/min/numéro, 6e refusée poliment.
10. Numéro USSD inconnu → message d'aide.
11. Option invalide → ré-affiche le menu.
12. Signature HMAC : valide passe, fausse renvoie 401.
13. Delivery report : SENT → DELIVERED via webhook.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
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
from app.modules.diplomas.enums import DiplomaStatus, DiplomaType
from app.modules.diplomas.models import Diploma
from app.modules.notifications.models import NotificationTemplate
from app.modules.sms.enums import SmsDirection, SmsStatus
from app.modules.sms.models import SmsMessage, UssdSession
from app.modules.sms.providers import (
    MockProvider,
    reset_provider_cache,
    set_provider,
)
from app.modules.sms.service import SmsService
from app.shared.base import generate_cuid
from app.shared.enums import UserRole
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def _fresh_mock_provider() -> Any:
    """Reset the singleton provider before EACH test (compteur monotone).

    Garantit l'isolation : un test qui envoie 3 SMS verra provider_id
    ``mock-00000001..3`` ; le suivant repart à 1.
    """
    reset_provider_cache()
    set_provider(MockProvider())
    yield
    reset_provider_cache()


@pytest_asyncio.fixture(loop_scope="session")
async def _no_hmac_secret() -> Any:
    """Garantit que USSD_HMAC_SECRET est vide pour les tests qui ne le testent pas."""
    previous = os.environ.pop("USSD_HMAC_SECRET", None)
    yield
    if previous is not None:
        os.environ["USSD_HMAC_SECRET"] = previous


@pytest_asyncio.fixture(loop_scope="session")
async def parent_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Crée un élève avec un guardianPhone normalisé + bulletin + diplôme.

    Le numéro ``+224622112233`` est utilisé par plusieurs tests USSD.
    """
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()

    student = await factories.StudentFactory.create_async(
        schoolId=tree["school"].id,
        firstName="Aissatou",
        lastName="Diallo",
        guardianPhone="+224622112233",
        guardianName="Mama Diallo",
    )

    # Année scolaire + période + bulletin (pour option 1)
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
        average=14.75,
        rank=3,
        totalStudents=42,
        verificationCode=f"RC-{generate_cuid()[:12]}",
    )
    db_session.add(report)
    await db_session.flush()

    # Diplôme ISSUED pour option 3
    diploma = Diploma(
        id=generate_cuid(),
        serial="CEPE-2026-ABCDEF12",
        studentId=student.id,
        diplomaType=DiplomaType.CEPE,
        schoolId=tree["school"].id,
        status=DiplomaStatus.ISSUED,
        payloadSha256="a" * 64,
        signature="b" * 80,
        publicKeyFingerprint="c" * 32,
        issuedAt=datetime.now(UTC),
        signedAt=datetime.now(UTC),
    )
    db_session.add(diploma)
    await db_session.flush()

    return {
        "tree": tree,
        "student": student,
        "school_year": school_year,
        "period": period,
        "report": report,
        "diploma": diploma,
    }


# ===========================================================================
# 1. Provider mock persists SmsMessage
# ===========================================================================
@pytest.mark.asyncio
async def test_mock_provider_send_persists_message(
    db_session: AsyncSession,
) -> None:
    factories.bind(db_session)
    service = SmsService(db_session)
    msg = await service.send_sms(to="+224622000001", body="hello")

    assert msg.id
    assert msg.status == SmsStatus.SENT
    assert msg.providerId is not None
    assert msg.direction == SmsDirection.OUTBOUND
    assert msg.to_ == "+224622000001"

    # Vérifie qu'il y a bien une ligne en DB
    rows = (
        await db_session.execute(
            select(SmsMessage).where(SmsMessage.id == msg.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].body == "hello"


# ===========================================================================
# 2. RBAC sur POST /send
# ===========================================================================
@pytest.mark.asyncio
async def test_send_endpoint_requires_director(
    client: AsyncClient, auth_headers, _no_hmac_secret,
) -> None:
    """Un TEACHER doit recevoir 403, un SCHOOL_DIRECTOR doit pouvoir envoyer."""
    teacher_headers = await auth_headers(UserRole.TEACHER)
    r_forbidden = await client.post(
        "/api/sms/send",
        json={"to": "+224622112233", "body": "test"},
        headers=teacher_headers,
    )
    assert r_forbidden.status_code == 403, r_forbidden.text

    director_headers = await auth_headers(UserRole.SCHOOL_DIRECTOR)
    r_ok = await client.post(
        "/api/sms/send",
        json={"to": "+224622112233", "body": "test"},
        headers=director_headers,
    )
    assert r_ok.status_code == 202, r_ok.text
    body = r_ok.json()
    assert body["status"] == "SENT"
    assert body["to"] == "+224622112233"


# ===========================================================================
# 3. send_templated avec langue préférée du user
# ===========================================================================
@pytest.mark.asyncio
async def test_send_templated_uses_user_language(
    db_session: AsyncSession,
) -> None:
    """Un user en ``ff`` doit recevoir le SMS dans le template ff (préfixé [ff])."""
    factories.bind(db_session)

    # Seed deux templates pour la clé "validation.approved" en fr et ff
    fr_template = NotificationTemplate(
        id=generate_cuid(),
        key="validation.approved", language="fr", channel="sms",
        subject=None,
        body="Votre demande {{entityLabel}} a ete approuvee.",
    )
    ff_template = NotificationTemplate(
        id=generate_cuid(),
        key="validation.approved", language="ff", channel="sms",
        subject=None,
        body="[ff] Wiɗto maa {{entityLabel}} jaɓaama.",
    )
    db_session.add_all([fr_template, ff_template])
    await db_session.flush()

    parent_fr = await factories.UserFactory.create_async(
        email="+224622777001",  # email = numero pour resolve simple
        preferredLanguage="fr",
        role=UserRole.SCHOOL_DIRECTOR,
    )
    parent_ff = await factories.UserFactory.create_async(
        email="+224622777002",
        preferredLanguage="ff",
        role=UserRole.SCHOOL_DIRECTOR,
    )

    service = SmsService(db_session)
    msg_fr = await service.send_templated(
        user_id=parent_fr.id,
        template_key="validation.approved",
        variables={"entityLabel": "Ecole X"},
    )
    msg_ff = await service.send_templated(
        user_id=parent_ff.id,
        template_key="validation.approved",
        variables={"entityLabel": "Ecole X"},
    )
    assert "approuvee" in msg_fr.body
    assert msg_ff.body.startswith("[ff]")
    assert "Ecole X" in msg_fr.body
    assert "Ecole X" in msg_ff.body


# ===========================================================================
# 4. USSD callback — menu d'accueil sur texte vide
# ===========================================================================
@pytest.mark.asyncio
async def test_ussd_callback_returns_welcome_menu_on_empty_text(
    client: AsyncClient, _no_hmac_secret,
) -> None:
    r = await client.post(
        "/api/sms/ussd/callback",
        json={
            "sessionId": "sess-welcome-1",
            "phoneNumber": "+224622000999",
            "serviceCode": "*999#",
            "text": "",
        },
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert body.startswith("CON ")
    assert "Bienvenue GESTION-EE" in body
    assert "1. Moyenne" in body
    assert "2. Presence" in body
    assert "3. Verifier diplome" in body


# ===========================================================================
# 5. Option 1 — moyenne pour un parent connu
# ===========================================================================
@pytest.mark.asyncio
async def test_ussd_option_1_returns_average_for_known_student(
    client: AsyncClient, parent_ctx: dict[str, Any], _no_hmac_secret,
) -> None:
    r = await client.post(
        "/api/sms/ussd/callback",
        json={
            "sessionId": "sess-avg-1",
            "phoneNumber": "+224622112233",
            "serviceCode": "*999#",
            "text": "1",
        },
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert body.startswith("END ")
    assert "14.75" in body
    assert "Aissatou" in body


# ===========================================================================
# 6. Option 1 — numéro inconnu = message d'aide
# ===========================================================================
@pytest.mark.asyncio
async def test_ussd_option_1_unknown_student_returns_helpful_error(
    client: AsyncClient, _no_hmac_secret,
) -> None:
    r = await client.post(
        "/api/sms/ussd/callback",
        json={
            "sessionId": "sess-unknown-1",
            "phoneNumber": "+224622000777",  # pas de student attaché
            "serviceCode": "*999#",
            "text": "1",
        },
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert body.startswith("END ")
    assert "non reconnu" in body.lower() or "contactez" in body.lower()


# ===========================================================================
# 7. Option 3 — diplôme via code élève
# ===========================================================================
@pytest.mark.asyncio
async def test_ussd_option_3_returns_diploma_status(
    client: AsyncClient, parent_ctx: dict[str, Any], _no_hmac_secret,
) -> None:
    serial = parent_ctx["diploma"].serial
    r = await client.post(
        "/api/sms/ussd/callback",
        json={
            "sessionId": "sess-diploma-1",
            "phoneNumber": "+224622000666",
            "serviceCode": "*999#",
            "text": f"3*{serial}",
        },
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert body.startswith("END ")
    assert "VALIDE" in body
    assert serial in body
    assert "CEPE" in body


# ===========================================================================
# 8. Session persistée + reprise
# ===========================================================================
@pytest.mark.asyncio
async def test_ussd_session_persisted_and_resumed(
    client: AsyncClient, db_session: AsyncSession, _no_hmac_secret,
) -> None:
    session_id = "sess-resume-1"
    # 1er appel — texte vide → menu d'accueil
    r1 = await client.post(
        "/api/sms/ussd/callback",
        json={
            "sessionId": session_id,
            "phoneNumber": "+224622111000",
            "serviceCode": "*999#",
            "text": "",
        },
    )
    assert r1.status_code == 200
    # 2e appel — sélection option 3 (incomplet : pas de serial)
    r2 = await client.post(
        "/api/sms/ussd/callback",
        json={
            "sessionId": session_id,
            "phoneNumber": "+224622111000",
            "serviceCode": "*999#",
            "text": "3",
        },
    )
    assert r2.status_code == 200
    assert r2.text.startswith("CON ")

    # Vérifie qu'il y a UNE seule UssdSession en DB et son étape suit
    rows = (
        await db_session.execute(
            select(UssdSession).where(UssdSession.sessionId == session_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].currentStep == "DIPLOMA_INPUT"


# ===========================================================================
# 9. Rate limit USSD — 5/min/numéro
# ===========================================================================
@pytest.mark.asyncio
async def test_ussd_rate_limit_5_per_minute_per_phone(
    client: AsyncClient, _no_hmac_secret,
) -> None:
    phone = "+224622300300"
    # Les 5 premières sessions doivent passer
    for i in range(5):
        r = await client.post(
            "/api/sms/ussd/callback",
            json={
                "sessionId": f"sess-ratelimit-{i}",
                "phoneNumber": phone,
                "serviceCode": "*999#",
                "text": "",
            },
        )
        assert r.status_code == 200, f"req #{i + 1} should pass: {r.text}"

    # La 6e doit être refusée (mais poliment, status 200 USSD)
    r = await client.post(
        "/api/sms/ussd/callback",
        json={
            "sessionId": "sess-ratelimit-extra",
            "phoneNumber": phone,
            "serviceCode": "*999#",
            "text": "",
        },
    )
    assert r.status_code == 200
    body = r.text
    assert body.startswith("END ")
    assert "Trop" in body or "trop" in body or "reess" in body.lower()


# ===========================================================================
# 10. Numéro USSD inconnu — message d'aide
# ===========================================================================
@pytest.mark.asyncio
async def test_ussd_unknown_phone_returns_message(
    client: AsyncClient, _no_hmac_secret,
) -> None:
    """Si le numéro ne matche aucun guardianPhone, message clair."""
    r = await client.post(
        "/api/sms/ussd/callback",
        json={
            "sessionId": "sess-ghost-1",
            "phoneNumber": "+224622990099",
            "serviceCode": "*999#",
            "text": "2",
        },
    )
    assert r.status_code == 200
    body = r.text
    assert body.startswith("END ")
    assert "reconnu" in body.lower() or "tuteur" in body.lower() or "ecole" in body.lower()


# ===========================================================================
# 11. Option invalide → menu redonné
# ===========================================================================
@pytest.mark.asyncio
async def test_ussd_invalid_option_returns_menu_again(
    client: AsyncClient, _no_hmac_secret,
) -> None:
    r = await client.post(
        "/api/sms/ussd/callback",
        json={
            "sessionId": "sess-invalid-1",
            "phoneNumber": "+224622400400",
            "serviceCode": "*999#",
            "text": "9",  # option inexistante
        },
    )
    assert r.status_code == 200
    body = r.text
    assert body.startswith("CON ")
    assert "invalide" in body.lower()
    assert "Bienvenue GESTION-EE" in body


# ===========================================================================
# 12. Signature HMAC — valide/invalide
# ===========================================================================
@pytest.mark.asyncio
async def test_ussd_signature_validation_when_secret_set(
    client: AsyncClient,
) -> None:
    secret = "super-secret-test-hmac"
    os.environ["USSD_HMAC_SECRET"] = secret
    try:
        payload = {
            "sessionId": "sess-hmac-1",
            "phoneNumber": "+224622500500",
            "serviceCode": "*999#",
            "text": "",
        }
        body_bytes = json.dumps(payload).encode("utf-8")
        good_sig = hmac.new(
            secret.encode("utf-8"), body_bytes, hashlib.sha256
        ).hexdigest()

        # Sans signature → 401
        r_missing = await client.post(
            "/api/sms/ussd/callback",
            content=body_bytes,
            headers={"content-type": "application/json"},
        )
        assert r_missing.status_code == 401, r_missing.text

        # Fausse signature → 401
        r_bad = await client.post(
            "/api/sms/ussd/callback",
            content=body_bytes,
            headers={
                "content-type": "application/json",
                "X-USSD-Signature": "deadbeef" * 8,
            },
        )
        assert r_bad.status_code == 401, r_bad.text

        # Bonne signature → 200 + menu
        r_ok = await client.post(
            "/api/sms/ussd/callback",
            content=body_bytes,
            headers={
                "content-type": "application/json",
                "X-USSD-Signature": good_sig,
            },
        )
        assert r_ok.status_code == 200, r_ok.text
        assert "Bienvenue GESTION-EE" in r_ok.text
    finally:
        os.environ.pop("USSD_HMAC_SECRET", None)


# ===========================================================================
# 13. Delivery report — webhook SENT → DELIVERED
# ===========================================================================
@pytest.mark.asyncio
async def test_sms_status_updated_on_provider_callback(
    client: AsyncClient, db_session: AsyncSession, _no_hmac_secret,
) -> None:
    # Envoie un SMS qui sera SENT (MockProvider)
    factories.bind(db_session)
    service = SmsService(db_session)
    msg = await service.send_sms(to="+224622600600", body="test delivery")
    assert msg.status == SmsStatus.SENT
    assert msg.providerId

    # Le webhook arrive : passe en DELIVERED
    r = await client.post(
        "/api/sms/delivery-report",
        json={
            "providerId": msg.providerId,
            "status": "DELIVERED",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "DELIVERED"
    assert body["providerId"] == msg.providerId

    # Vérifie en DB
    refreshed = await db_session.get(SmsMessage, msg.id)
    assert refreshed is not None
    assert refreshed.status == SmsStatus.DELIVERED
    assert refreshed.deliveredAt is not None
