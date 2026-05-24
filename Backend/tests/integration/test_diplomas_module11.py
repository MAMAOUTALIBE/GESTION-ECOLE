"""Module 11 — Diplômes signés Ed25519, vérification publique.

Couvre :

1. Primitives crypto (canonicalisation, signature, hash).
2. Sérialisation : format ``{TYPE}-{YEAR}-{8HEX}``.
3. Service : émission, persistance, audit log.
4. Endpoint public ``/verify`` : VALID, REVOKED, NOT_FOUND.
5. RBAC : émission (MINISTRY_ADMIN), révocation (NATIONAL_ADMIN), listing
   (scope école).
6. Anti-leak : aucun ID interne dans la réponse publique.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.diplomas.crypto import (
    canonicalize_payload,
    compute_payload_sha256,
    get_public_key_pem,
    reset_signing_key_cache,
    sign_payload,
    verify_signature,
)
from app.modules.diplomas.enums import DiplomaStatus, DiplomaType
from app.modules.diplomas.models import Diploma
from app.modules.diplomas.serial import generate_serial
from app.modules.diplomas.service import DiplomaService
from app.modules.workflow.models import AuditLog
from app.shared.enums import UserRole
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Crypto key isolation : on force la régénération d'un keypair éphémère
# au début de la session de tests (et on s'assure qu'aucune env var ne traîne).
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _ephemeral_signing_key() -> None:
    import os

    os.environ.pop("DIPLOMA_SIGNING_KEY_PEM", None)
    reset_signing_key_cache()
    # Premier appel = génération de la clé. Toutes les signatures de la
    # session utiliseront cette même clé.
    from app.modules.diplomas.crypto import load_or_generate_signing_key
    load_or_generate_signing_key()


# ---------------------------------------------------------------------------
# Helpers fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def school_ctx(db_session: AsyncSession) -> dict[str, Any]:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    student = await factories.StudentFactory.create_async(
        schoolId=tree["school"].id,
        firstName="Aïssatou",
        lastName="Diallo",
    )
    return {
        "region": tree["region"],
        "prefecture": tree["prefecture"],
        "subPrefecture": tree["subPrefecture"],
        "school": tree["school"],
        "student": student,
    }


@pytest_asyncio.fixture(loop_scope="session")
async def ministry_headers(auth_headers: Any) -> dict[str, str]:
    return await auth_headers(UserRole.MINISTRY_ADMIN)


@pytest_asyncio.fixture(loop_scope="session")
async def national_headers(auth_headers: Any) -> dict[str, str]:
    return await auth_headers(UserRole.NATIONAL_ADMIN)


@pytest_asyncio.fixture(loop_scope="session")
async def director_headers(
    auth_headers: Any, school_ctx: dict[str, Any],
) -> dict[str, str]:
    return await auth_headers(
        UserRole.SCHOOL_DIRECTOR,
        regionId=school_ctx["region"].id,
        prefectureId=school_ctx["prefecture"].id,
        subPrefectureId=school_ctx["subPrefecture"].id,
        schoolId=school_ctx["school"].id,
    )


@pytest_asyncio.fixture(loop_scope="session")
async def other_director_headers(
    auth_headers: Any, db_session: AsyncSession,
) -> dict[str, str]:
    factories.bind(db_session)
    other = await factories.make_territorial_tree()
    return await auth_headers(
        UserRole.SCHOOL_DIRECTOR,
        regionId=other["region"].id,
        prefectureId=other["prefecture"].id,
        subPrefectureId=other["subPrefecture"].id,
        schoolId=other["school"].id,
    )


async def _issue_via_service(
    db_session: AsyncSession,
    school_ctx: dict[str, Any],
    *,
    diploma_type: DiplomaType = DiplomaType.CEPE,
    score: float | None = 14.5,
    actor_role: UserRole = UserRole.MINISTRY_ADMIN,
) -> Diploma:
    """Crée un User actor + appelle DiplomaService.issue_diploma."""
    factories.bind(db_session)
    actor = await factories.UserFactory.create_async(role=actor_role)
    svc = DiplomaService(db_session)
    return await svc.issue_diploma(
        student_id=school_ctx["student"].id,
        diploma_type=diploma_type,
        school_id=school_ctx["school"].id,
        actor=actor,
        score=score,
        mention="Bien" if score is not None and score >= 14 else None,
        exam_center="Conakry-Centre",
    )


# ===========================================================================
# 1. Crypto primitives
# ===========================================================================
def test_canonicalize_payload_sorts_keys() -> None:
    """Les clés doivent être triées récursivement, sans espace."""
    a = canonicalize_payload({"b": 2, "a": 1, "nested": {"y": "z", "x": 1}})
    # Tri attendu : "a" avant "b" ; à l'intérieur de "nested", x avant y.
    assert a == b'{"a":1,"b":2,"nested":{"x":1,"y":"z"}}'

    # L'ordre d'écriture des clés ne doit jamais changer la sortie.
    b_payload = canonicalize_payload(
        {"nested": {"y": "z", "x": 1}, "a": 1, "b": 2}
    )
    assert a == b_payload


def test_sign_and_verify_roundtrip() -> None:
    payload = {
        "serial": "CEPE-2026-3F2A91BC",
        "score": 16.5,
        "student": {"first_name": "Aïssatou", "last_name": "Diallo"},
    }
    sig_b64, fingerprint = sign_payload(payload)
    assert isinstance(sig_b64, str) and len(sig_b64) >= 80
    assert isinstance(fingerprint, str) and len(fingerprint) == 32

    pem = get_public_key_pem()
    assert verify_signature(payload, sig_b64, pem) is True


def test_verify_fails_on_tampered_payload() -> None:
    payload = {"serial": "CEPE-2026-DEADBEEF", "score": 10.0}
    sig_b64, _ = sign_payload(payload)
    pem = get_public_key_pem()

    # Modification d'un seul champ → la signature doit être rejetée.
    tampered = dict(payload, score=20.0)
    assert verify_signature(tampered, sig_b64, pem) is False


def test_payload_sha256_changes_when_score_changes() -> None:
    base = {"serial": "BEPC-2026-AABBCCDD", "score": 12.0, "mention": "Passable"}
    h1 = compute_payload_sha256(base)
    h2 = compute_payload_sha256(dict(base, score=12.5))
    assert h1 != h2
    # Garde-fou : un même payload doit produire le même hash (déterminisme).
    assert h1 == compute_payload_sha256(dict(base))


# ===========================================================================
# 2. Serial format
# ===========================================================================
def test_generate_serial_format() -> None:
    serial = generate_serial("CEPE", 2026)
    assert re.match(r"^CEPE-2026-[0-9A-F]{8}$", serial), serial
    # Deux appels → deux serials différents (randomness).
    assert generate_serial("CEPE", 2026) != generate_serial("CEPE", 2026)
    assert generate_serial("BEPC", 2027).startswith("BEPC-2027-")


# ===========================================================================
# 3. Service — persistance + audit
# ===========================================================================
@pytest.mark.asyncio
async def test_issue_diploma_persists_with_signature(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    diploma = await _issue_via_service(db_session, school_ctx, score=15.25)

    assert diploma.id and len(diploma.id) <= 30
    assert diploma.status == DiplomaStatus.ISSUED
    assert diploma.serial.startswith("CEPE-")
    # Signature, hash, fingerprint tous présents
    assert diploma.signature and len(diploma.signature) >= 80
    assert diploma.payloadSha256 and len(diploma.payloadSha256) == 64
    assert diploma.publicKeyFingerprint and len(diploma.publicKeyFingerprint) == 32
    assert diploma.signedAt is not None
    assert diploma.issuedAt is not None


@pytest.mark.asyncio
async def test_issue_creates_audit_log_entry(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    diploma = await _issue_via_service(db_session, school_ctx)

    log = (await db_session.execute(
        select(AuditLog).where(
            AuditLog.entity == "Diploma",
            AuditLog.entityId == diploma.id,
            AuditLog.action == "ISSUE_DIPLOMA",
        ),
    )).scalar_one_or_none()

    assert log is not None
    assert log.metadata_ is not None
    assert log.metadata_["serial"] == diploma.serial
    assert log.metadata_["diplomaType"] == DiplomaType.CEPE.value
    assert log.metadata_["studentId"] == school_ctx["student"].id


# ===========================================================================
# 4. Endpoint public /verify
# ===========================================================================
@pytest.mark.asyncio
async def test_verify_diploma_endpoint_public_no_auth_returns_valid(
    client: AsyncClient,
    db_session: AsyncSession,
    school_ctx: dict[str, Any],
) -> None:
    diploma = await _issue_via_service(db_session, school_ctx, score=17.0)

    # PUBLIC : pas de headers Authorization.
    r = await client.get(f"/api/diplomas/verify/{diploma.serial}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "VALID"
    assert body["serial"] == diploma.serial
    assert body["diplomaType"] == DiplomaType.CEPE.value
    assert body["score"] == 17.0
    assert body["payloadSha256"] == diploma.payloadSha256
    assert body["signature"] == diploma.signature
    # Le payload signé est inclus pour vérification offline.
    assert body["payload"] is not None
    assert body["payload"]["serial"] == diploma.serial

    # La signature retournée doit vérifier contre la clé publique courante.
    pem = get_public_key_pem()
    assert verify_signature(body["payload"], body["signature"], pem) is True


@pytest.mark.asyncio
async def test_verify_unknown_serial_returns_404(client: AsyncClient) -> None:
    r = await client.get("/api/diplomas/verify/CEPE-2026-DEADBEEF")
    assert r.status_code == 404, r.text
    body = r.json()
    # Body structuré conforme au schema, pas un message d'erreur libre.
    assert body["status"] == "NOT_FOUND"
    assert body["serial"] == "CEPE-2026-DEADBEEF"


@pytest.mark.asyncio
async def test_verify_revoked_diploma_returns_revoked_status(
    client: AsyncClient,
    db_session: AsyncSession,
    school_ctx: dict[str, Any],
) -> None:
    diploma = await _issue_via_service(db_session, school_ctx)
    # Révocation directe via service (avec un acteur NATIONAL_ADMIN)
    factories.bind(db_session)
    admin = await factories.UserFactory.create_async(role=UserRole.NATIONAL_ADMIN)
    svc = DiplomaService(db_session)
    await svc.revoke_diploma(
        diploma.serial, reason="Erreur de saisie du score", actor=admin,
    )

    r = await client.get(f"/api/diplomas/verify/{diploma.serial}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "REVOKED"
    assert body["revokedReason"] == "Erreur de saisie du score"
    assert body["revokedAt"] is not None
    # Ne doit PAS contenir la signature (pour ne pas induire en erreur le
    # vérificateur — la signature reste mathématiquement valide).
    assert body.get("signature") is None
    assert body.get("payload") is None


# ===========================================================================
# 5. RBAC
# ===========================================================================
@pytest.mark.asyncio
async def test_issue_requires_ministry_admin(
    client: AsyncClient,
    director_headers: dict[str, str],
    school_ctx: dict[str, Any],
) -> None:
    payload = {
        "studentId": school_ctx["student"].id,
        "schoolId": school_ctx["school"].id,
        "diplomaType": DiplomaType.CEPE.value,
        "score": 13.0,
    }
    r = await client.post(
        "/api/diplomas", headers=director_headers, json=payload,
    )
    # Un SCHOOL_DIRECTOR ne peut PAS émettre.
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_revoke_diploma_requires_national_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    school_ctx: dict[str, Any],
    ministry_headers: dict[str, str],
) -> None:
    diploma = await _issue_via_service(db_session, school_ctx)
    # MINISTRY_ADMIN peut émettre mais PAS révoquer (NATIONAL_ADMIN only).
    r = await client.post(
        f"/api/diplomas/{diploma.serial}/revoke",
        headers=ministry_headers,
        json={"reason": "Test"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_list_diplomas_respects_scope(
    client: AsyncClient,
    db_session: AsyncSession,
    school_ctx: dict[str, Any],
    director_headers: dict[str, str],
    other_director_headers: dict[str, str],
) -> None:
    # On émet 1 diplôme dans school_ctx (école A)
    await _issue_via_service(db_session, school_ctx)

    # Directeur de l'école A : voit 1 diplôme
    r_a = await client.get("/api/diplomas", headers=director_headers)
    assert r_a.status_code == 200, r_a.text
    body_a = r_a.json()
    assert body_a["total"] >= 1
    # Tous les diplômes retournés sont de l'école A.
    for d in body_a["items"]:
        assert d["schoolId"] == school_ctx["school"].id

    # Directeur d'une autre école B : ne voit rien.
    r_b = await client.get("/api/diplomas", headers=other_director_headers)
    assert r_b.status_code == 200, r_b.text
    body_b = r_b.json()
    assert body_b["total"] == 0
    assert body_b["items"] == []


@pytest.mark.asyncio
async def test_revoked_diploma_cannot_be_re_issued_with_same_serial(
    db_session: AsyncSession,
    school_ctx: dict[str, Any],
) -> None:
    """Une fois révoqué, le serial reste réservé : on ne peut pas le
    réémettre. Concrètement, une nouvelle émission génère un nouveau
    serial → le serial révoqué reste unique et historique."""
    diploma = await _issue_via_service(db_session, school_ctx)
    original_serial = diploma.serial

    factories.bind(db_session)
    admin = await factories.UserFactory.create_async(role=UserRole.NATIONAL_ADMIN)
    svc = DiplomaService(db_session)
    await svc.revoke_diploma(
        original_serial, reason="Fraud detected", actor=admin,
    )

    # Re-émission : nouveau serial obligatoire (l'ancien reste en DB).
    new_diploma = await _issue_via_service(db_session, school_ctx)
    assert new_diploma.serial != original_serial

    # L'ancien diplôme reste accessible et révoqué.
    old = (await db_session.execute(
        select(Diploma).where(Diploma.serial == original_serial),
    )).scalar_one()
    assert old.status == DiplomaStatus.REVOKED


# ===========================================================================
# 6. Privacy — pas de leak d'IDs internes
# ===========================================================================
@pytest.mark.asyncio
async def test_verify_response_does_not_leak_internal_ids(
    client: AsyncClient,
    db_session: AsyncSession,
    school_ctx: dict[str, Any],
) -> None:
    diploma = await _issue_via_service(db_session, school_ctx)

    r = await client.get(f"/api/diplomas/verify/{diploma.serial}")
    assert r.status_code == 200, r.text
    body = r.json()

    # Le body sérialisé entier ne doit JAMAIS contenir :
    # - le studentId interne (cuid 25 chars)
    # - le schoolId interne
    # - le diploma.id interne
    # - une date de naissance, un guardian phone, etc.
    raw = r.text
    assert school_ctx["student"].id not in raw
    assert school_ctx["school"].id not in raw
    assert diploma.id not in raw

    # Le student exposé n'a que firstName + initial du nom + nom d'école.
    student_info = body["student"]
    assert set(student_info.keys()) == {
        "firstName", "lastNameInitial", "schoolName",
    }
    assert student_info["firstName"] == "Aïssatou"
    # Initiale du nom uniquement (anti-PII renforcé).
    assert student_info["lastNameInitial"] == "D."
