"""Module 2 — Census dédoublonnage fuzzy & normalisation.

Couvre 17 cas + 8 régressions Module 2.1 (review CRITICAL) :
* 1-2  : normalize_name (accents, apostrophes/tirets)
* 3-4  : normalize_phone_guinea (formats valides / rejets)
* 5-6  : validate_birthdate_for_classroom (CP normal / trop jeune)
* 7-9  : compute_similarity_score (HIGH / LOW / MEDIUM)
* 10   : endpoint check-duplicates retourne ordré
* 11-12: create_student bloque/force avec audit
* 13-15: merge_students (transferts / RBAC / idempotence)
* 16   : check-duplicates respecte le scope territorial
* 17   : create_teacher dédoublonne aussi

Régressions Module 2.1 (CRITICAL review) :
* C-1a : cas marketing Aichatou/Aissatou sans birth/phone → HIGH (était LOW)
* C-1b : poids renormalisés quand champs optionnels absents
* C-1c : exact-match legacy bloque même sans HIGH fuzzy (sans force)
* C-2  : firstName > 120 chars → 422
* C-3a : TEACHER ne peut pas appeler /check-duplicates → 403
* C-3b : /check-duplicates ne renvoie pas de birthDate exacte
* M-6a : create_student rejette birthDate incohérente avec niveau classe
* M-6b : force=true sur incohérence d'âge → succès + audit override
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import Parent, ParentCommunication, StudentParent
from app.modules.attendance.models import AttendanceRecord
from app.modules.census.duplicates import (
    classify_score,
    compute_similarity_score,
)
from app.modules.census.models import Student
from app.modules.census.normalization import (
    normalize_name,
    normalize_phone_guinea,
    validate_birthdate_for_classroom,
)
from app.modules.workflow.models import AuditLog
from app.shared.base import generate_cuid
from app.shared.enums import (
    AttendanceStatus,
    CommunicationChannel,
    CommunicationStatus,
    Gender,
    ParentRelationType,
    PersonType,
    UserRole,
)
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _make_school(db_session: AsyncSession) -> tuple[str, str]:
    """Crée un tree territorial minimum et renvoie (school_id, region_id)."""
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    return tree["school"].id, tree["region"].id


async def _make_two_schools_in_diff_regions(
    db_session: AsyncSession,
) -> dict:
    """Deux régions distinctes avec une école chacune (pour tests de scope)."""
    factories.bind(db_session)
    r1 = await factories.RegionFactory.create_async()
    r2 = await factories.RegionFactory.create_async()
    s1 = await factories.SchoolFactory.create_async(regionId=r1.id)
    s2 = await factories.SchoolFactory.create_async(regionId=r2.id)
    return {"r1": r1, "r2": r2, "s1": s1, "s2": s2}


# ---------------------------------------------------------------------------
# 1. normalize_name — accents + case
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("AÏSSATOU diallo", "Aïssatou Diallo"),
        ("  mamadou  bah  ", "Mamadou Bah"),
        ("ABDOULAYE SOW", "Abdoulaye Sow"),
        ("kadiatou camara", "Kadiatou Camara"),
    ],
)
def test_normalize_name_handles_accents_and_case(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


# ---------------------------------------------------------------------------
# 2. normalize_name — apostrophes & tirets
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("n'diaye-sow", "N'Diaye-Sow"),
        ("sidi-bah", "Sidi-Bah"),
        ("d'aubigné", "D'Aubigné"),
        ("ka’dieto", "Ka'Dieto"),  # apostrophe typographique normalisée
    ],
)
def test_normalize_name_preserves_apostrophes_and_dashes(
    raw: str, expected: str
) -> None:
    assert normalize_name(raw) == expected


# ---------------------------------------------------------------------------
# 3. normalize_phone_guinea — formats valides
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        "622123456",
        "+224622123456",
        "00224622123456",
        "224622123456",
        "+224 622 12 34 56",
        "+224-622-12-34-56",
    ],
)
def test_normalize_phone_guinea_valid_formats(raw: str) -> None:
    assert normalize_phone_guinea(raw) == "+224622123456"


def test_normalize_phone_guinea_none_and_empty_return_none() -> None:
    assert normalize_phone_guinea(None) is None
    assert normalize_phone_guinea("") is None
    assert normalize_phone_guinea("   ") is None


# ---------------------------------------------------------------------------
# 4. normalize_phone_guinea — rejets
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        "+33612345678",  # France
        "+1234567890",   # USA
        "0033612345678", # France via 00
        "12345",         # trop court
        "62212345",      # trop court (8 chiffres)
        "722123456",     # préfixe non-mobile
        "abcdef1234",    # non numérique
        "+22462212345",  # 8 chiffres après code pays
    ],
)
def test_normalize_phone_guinea_invalid_raises(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_phone_guinea(raw)


# ---------------------------------------------------------------------------
# 5. validate_birthdate — CP normal (6 ans à la rentrée)
# ---------------------------------------------------------------------------
def test_validate_birthdate_cp_normal() -> None:
    # Rentrée fictive : 2026-10-01 → un enfant né en juin 2020 a 6 ans → CP OK.
    ref = date(2026, 10, 15)
    ok, reason = validate_birthdate_for_classroom(
        date(2020, 6, 15), "CP", reference_date=ref
    )
    assert ok, reason
    assert reason is None


def test_validate_birthdate_without_level_uses_global_range() -> None:
    ref = date(2026, 10, 15)
    ok, _ = validate_birthdate_for_classroom(date(2018, 1, 1), None, reference_date=ref)
    assert ok  # 8 ans à la rentrée → dans la plage 3-16


# ---------------------------------------------------------------------------
# 6. validate_birthdate — CP trop jeune
# ---------------------------------------------------------------------------
def test_validate_birthdate_cp_too_young() -> None:
    ref = date(2026, 10, 15)
    # Né en 2022 → 4 ans → trop jeune pour CP
    ok, reason = validate_birthdate_for_classroom(
        date(2022, 6, 15), "CP", reference_date=ref
    )
    assert not ok
    assert reason is not None
    assert "4" in reason or "incohér" in reason.lower()


def test_validate_birthdate_future_date_rejected() -> None:
    ref = date(2026, 10, 15)
    ok, reason = validate_birthdate_for_classroom(
        date(2030, 1, 1), None, reference_date=ref
    )
    assert not ok
    assert "futur" in (reason or "").lower()


# ---------------------------------------------------------------------------
# 7. score — doublon évident (HIGH)
# ---------------------------------------------------------------------------
def test_similarity_score_obvious_duplicate_high() -> None:
    a = {
        "firstName": "Aïssatou",
        "lastName": "Diallo",
        "birthDate": date(2018, 3, 15),
        "guardianPhone": "+224622123456",
        "gender": Gender.FEMALE,
        "schoolId": "school-A",
    }
    b = {
        "firstName": "AISSATOU",
        "lastName": "DIALLO",
        "birthDate": date(2018, 3, 15),
        "guardianPhone": "+224622123456",
        "gender": Gender.FEMALE,
        "schoolId": "school-A",
    }
    result = compute_similarity_score(a, b)
    assert result["score"] >= 0.85
    assert classify_score(result["score"]) == "HIGH"
    assert "lastName" in result["matchedFields"]
    assert "birthDate" in result["matchedFields"]


# ---------------------------------------------------------------------------
# 8. score — homonymes réels avec birthdates différentes (LOW)
# ---------------------------------------------------------------------------
def test_similarity_score_real_homonyms_low() -> None:
    a = {
        "firstName": "Aminata",
        "lastName": "Camara",
        "birthDate": date(2015, 1, 1),
        "guardianPhone": None,
        "gender": Gender.FEMALE,
        "schoolId": "school-A",
    }
    b = {
        "firstName": "Aminata",
        "lastName": "Camara",
        "birthDate": date(2019, 11, 12),  # 4 ans d'écart
        "guardianPhone": None,
        "gender": Gender.FEMALE,
        "schoolId": "school-B",  # école différente
    }
    result = compute_similarity_score(a, b)
    # Sans birthdate match, sans phone, sans même école — on est sous MEDIUM.
    assert result["score"] < 0.65
    assert classify_score(result["score"]) == "LOW"


# ---------------------------------------------------------------------------
# 9. score — même nom/prénom sans phone ni birthdate → renormalisation
# ---------------------------------------------------------------------------
def test_similarity_score_partial_match_renormalizes_when_optional_fields_absent() -> None:
    """Régression C-1 review Module 2.

    AVANT fix : lastName(0.30) + firstName(0.20) + gender(0.05) + schoolId(0.05)
    = 0.60 (plafonné à 60% car les poids birthDate/phone étaient perdus).
    APRÈS fix : on renormalise sur la somme des poids actifs (0.60), donc
    deux fiches strictement identiques sur les champs présents matchent à
    1.0 (HIGH) — comportement attendu pour éviter les faux négatifs.
    """
    a = {
        "firstName": "Fatoumata",
        "lastName": "Bah",
        "birthDate": None,
        "guardianPhone": None,
        "gender": Gender.FEMALE,
        "schoolId": "school-X",
    }
    b = {
        "firstName": "Fatoumata",
        "lastName": "Bah",
        "birthDate": None,
        "guardianPhone": None,
        "gender": Gender.FEMALE,
        "schoolId": "school-X",
    }
    result = compute_similarity_score(a, b)
    # Renormalisation : 4 features actives sur 6 → score = somme_active / 0.60
    assert result["activeWeight"] == pytest.approx(0.60, abs=0.001)
    # Match exact sur les 4 features actives → score = 1.0 → HIGH
    assert classify_score(result["score"]) == "HIGH"


def test_similarity_score_with_phone_match_climbs_to_high() -> None:
    """Même nom/prénom + phone match (sans birthdate) → HIGH après renormalisation.

    AVANT fix : 0.30 + 0.20 + 0.15 + 0.05 + 0.05 = 0.75 (MEDIUM).
    APRÈS fix : 0.75 / 0.75 (poids actifs) = 1.0 (HIGH) — correct car deux
    fiches identiques sur 5 features dont le téléphone tuteur ne peuvent
    raisonnablement pas être deux personnes différentes.
    """
    a = {
        "firstName": "Fatoumata",
        "lastName": "Bah",
        "birthDate": None,
        "guardianPhone": "+224622987654",
        "gender": Gender.FEMALE,
        "schoolId": "school-X",
    }
    b = {
        "firstName": "Fatoumata",
        "lastName": "Bah",
        "birthDate": None,
        "guardianPhone": "+224622987654",
        "gender": Gender.FEMALE,
        "schoolId": "school-X",
    }
    result = compute_similarity_score(a, b)
    assert result["activeWeight"] == pytest.approx(0.75, abs=0.001)
    assert classify_score(result["score"]) == "HIGH"


# ---------------------------------------------------------------------------
# 10. endpoint /check-duplicates retourne les matches ordonnés
# ---------------------------------------------------------------------------
async def test_check_duplicates_endpoint_returns_matches_ordered_by_score(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, region_id = await _make_school(db_session)
    # Crée une paire de "vrais doublons"
    await factories.make_duplicate_pair(school_id)
    # Et un homonyme distant
    await factories.StudentFactory.create_async(
        schoolId=school_id,
        firstName="Aminata",
        lastName="Diallo",
        gender=Gender.FEMALE,
        birthDate=datetime(2010, 1, 1, tzinfo=UTC),
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    payload = {
        "firstName": "Aissatou",
        "lastName": "Diallo",
        "birthDate": "2018-03-15",
        "gender": "FEMALE",
        "guardianPhone": "+224622123456",
        "schoolId": school_id,
    }
    res = await client.post(
        "/api/census/students/check-duplicates",
        json=payload,
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] >= 1
    assert len(body["matches"]) >= 1
    # Ordre décroissant par score
    scores = [m["score"] for m in body["matches"]]
    assert scores == sorted(scores, reverse=True)
    # Le premier match est HIGH
    assert body["matches"][0]["classification"] == "HIGH"


# ---------------------------------------------------------------------------
# 11. create_student bloque sur HIGH duplicate sans force
# ---------------------------------------------------------------------------
async def test_create_student_blocks_on_high_duplicate_without_force(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, _ = await _make_school(db_session)
    await factories.make_duplicate_pair(school_id)

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    payload = {
        "firstName": "Aissatou",
        "lastName": "Diallo",
        "gender": "FEMALE",
        "birthDate": "2018-03-15",
        "guardianPhone": "+224622123456",
        "schoolId": school_id,
    }
    res = await client.post("/api/census/students", json=payload, headers=headers)
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["code"] == "conflict"
    assert "duplicates" in body["extra"]
    assert len(body["extra"]["duplicates"]) >= 1
    # Soit "EXACT" (barrière legacy même nom/prénom/birthDate/école), soit
    # "HIGH" (scoring fuzzy). Les deux protègent.
    assert body["extra"]["duplicates"][0]["classification"] in ("EXACT", "HIGH")


# ---------------------------------------------------------------------------
# 12. create_student avec force=true crée + ligne audit
# ---------------------------------------------------------------------------
async def test_create_student_succeeds_with_force_true_and_audits(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, _ = await _make_school(db_session)
    await factories.make_duplicate_pair(school_id)

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    payload = {
        "firstName": "Aissatou",
        "lastName": "Diallo",
        "gender": "FEMALE",
        "birthDate": "2018-03-15",
        "guardianPhone": "+224622123456",
        "schoolId": school_id,
    }
    res = await client.post(
        "/api/census/students?force=true", json=payload, headers=headers
    )
    assert res.status_code == 201, res.text
    body = res.json()
    new_id = body["id"]

    # Vérification audit
    audit_row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "CREATE_STUDENT",
                AuditLog.entityId == new_id,
            )
        )
    ).scalar_one()
    assert audit_row.metadata_ is not None
    assert audit_row.metadata_.get("reason") == "force_creation_after_duplicate_warning"
    assert "forcedDuplicates" in audit_row.metadata_


# ---------------------------------------------------------------------------
# 13. merge_students transfère toutes les rows dépendantes
# ---------------------------------------------------------------------------
async def test_merge_students_transfers_all_dependent_rows(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, region_id = await _make_school(db_session)
    source, target = await factories.make_duplicate_pair(school_id)

    # Ajoute des AttendanceRecord sur le source.
    for i in range(3):
        db_session.add(
            AttendanceRecord(
                personType=PersonType.STUDENT,
                status=AttendanceStatus.PRESENT,
                scannedAt=datetime(2026, 10, i + 1, 8, 0, tzinfo=UTC),
                schoolId=school_id,
                studentId=source.id,
            )
        )

    # Couverture H-5 (review Module 2.1 — bonus) : on wire aussi des
    # StudentParent + ParentCommunication pour vérifier que le merge
    # transfère bien ces entités sans laisser d'orphelin.
    parent = Parent(
        id=generate_cuid(),
        firstName="Mariam",
        lastName="Sow",
        phone=f"+22462{datetime.now(UTC).microsecond:07d}"[:13],
    )
    db_session.add(parent)
    await db_session.flush()

    db_session.add(
        StudentParent(
            id=generate_cuid(),
            studentId=source.id,
            parentId=parent.id,
            relation=ParentRelationType.MOTHER,
            isPrimary=True,
        )
    )
    db_session.add(
        ParentCommunication(
            id=generate_cuid(),
            parentId=parent.id,
            studentId=source.id,
            channel=CommunicationChannel.SMS,
            status=CommunicationStatus.SENT,
            message="Bulletin disponible (test merge).",
        )
    )
    await db_session.flush()

    headers = await auth_headers(UserRole.REGIONAL_ADMIN, regionId=region_id)
    res = await client.post(
        f"/api/census/students/{source.id}/merge",
        json={
            "targetId": target.id,
            "reason": "Doublon detecte par audit territorial (test).",
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["id"] == target.id

    # Toutes les attendances sont maintenant sur target
    target_attendances = (
        await db_session.execute(
            select(AttendanceRecord).where(AttendanceRecord.studentId == target.id)
        )
    ).scalars().all()
    assert len(target_attendances) == 3

    # StudentParent transféré (H-5 bonus)
    sp_rows = (
        await db_session.execute(
            select(StudentParent).where(StudentParent.studentId == target.id)
        )
    ).scalars().all()
    assert len(sp_rows) == 1
    # ParentCommunication transférée
    pc_rows = (
        await db_session.execute(
            select(ParentCommunication).where(ParentCommunication.studentId == target.id)
        )
    ).scalars().all()
    assert len(pc_rows) == 1

    # Source plus en DB
    src_check = (
        await db_session.execute(
            select(Student).where(Student.id == source.id)
        )
    ).scalar_one_or_none()
    assert src_check is None

    # AuditLog créé
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "MERGE_STUDENTS",
                AuditLog.entityId == target.id,
            )
        )
    ).scalar_one()
    assert audit.metadata_["source_id"] == source.id
    assert audit.metadata_["transferred"]["attendances"] == 3
    # Le ``reason`` est tracé dans l'audit (H-1).
    assert audit.metadata_.get("reason") is not None
    assert len(audit.metadata_["reason"]) >= 20
    # Rowcount cohérent pour StudentParent + ParentCommunication
    assert audit.metadata_["transferred"]["studentParents"] == 1
    assert audit.metadata_["transferred"]["parentCommunications"] == 1


# ---------------------------------------------------------------------------
# 14. merge_students RBAC bloque school_director
# ---------------------------------------------------------------------------
async def test_merge_students_rbac_blocks_school_director(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, _ = await _make_school(db_session)
    source, target = await factories.make_duplicate_pair(school_id)

    headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR, schoolId=school_id
    )
    res = await client.post(
        f"/api/census/students/{source.id}/merge",
        json={
            "targetId": target.id,
            "reason": "Test RBAC bloque les SCHOOL_DIRECTOR du merge.",
        },
        headers=headers,
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 15. merge_students idempotent (source déjà supprimé)
# ---------------------------------------------------------------------------
async def test_merge_students_idempotent_on_already_deleted_source(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, region_id = await _make_school(db_session)
    _, target = await factories.make_duplicate_pair(school_id)

    headers = await auth_headers(UserRole.REGIONAL_ADMIN, regionId=region_id)
    # source_id inventé → simule un source déjà supprimé.
    res = await client.post(
        "/api/census/students/cm0xxxxxxxxxxxxxxxxxghost/merge",
        json={
            "targetId": target.id,
            "reason": "Replay du merge apres source deja supprime (test).",
        },
        headers=headers,
    )
    # 200 OK (idempotent) — pas de 500
    assert res.status_code == 200, res.text
    assert res.json()["id"] == target.id

    # Si target inconnu → 404 propre
    res2 = await client.post(
        "/api/census/students/cm0xxxxxxxxxxxxxxxxxghost/merge",
        json={
            "targetId": "cm0yyyyyyyyyyyyyyyyyghost",
            "reason": "Tentative de merge vers target inconnu (test).",
        },
        headers=headers,
    )
    assert res2.status_code == 404, res2.text


# ---------------------------------------------------------------------------
# 16. check-duplicates respecte le scope territorial
# ---------------------------------------------------------------------------
async def test_check_duplicates_respects_territorial_scope(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    setup = await _make_two_schools_in_diff_regions(db_session)
    # Doublons "Aissatou Diallo" dans région 1 ET région 2
    await factories.StudentFactory.create_async(
        schoolId=setup["s1"].id,
        firstName="Aïssatou",
        lastName="Diallo",
        gender=Gender.FEMALE,
        birthDate=datetime(2018, 3, 15, tzinfo=UTC),
        guardianPhone="+224622123456",
    )
    await factories.StudentFactory.create_async(
        schoolId=setup["s2"].id,
        firstName="Aïssatou",
        lastName="Diallo",
        gender=Gender.FEMALE,
        birthDate=datetime(2018, 3, 15, tzinfo=UTC),
        guardianPhone="+224622123456",
    )

    # Un REGIONAL_ADMIN de la région 1 ne doit voir QUE le doublon de sa région.
    headers = await auth_headers(
        UserRole.REGIONAL_ADMIN, regionId=setup["r1"].id
    )
    res = await client.post(
        "/api/census/students/check-duplicates",
        json={
            "firstName": "Aissatou",
            "lastName": "Diallo",
            "birthDate": "2018-03-15",
            "guardianPhone": "+224622123456",
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 1, f"R1 admin doit voir 1 doublon, vu {body}"
    assert body["matches"][0]["schoolId"] == setup["s1"].id


# ---------------------------------------------------------------------------
# 17. create_teacher dédoublonne aussi
# ---------------------------------------------------------------------------
async def test_create_teacher_also_dedupes(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, _ = await _make_school(db_session)
    # Crée un teacher de référence
    await factories.TeacherFactory.create_async(
        schoolId=school_id,
        firstName="Mamadou",
        lastName="Bah",
        gender=Gender.MALE,
        birthDate=datetime(1985, 5, 20, tzinfo=UTC),
        phone="+224622111222",
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    payload = {
        "firstName": "MAMADOU",
        "lastName": "BAH",
        "gender": "MALE",
        "birthDate": "1985-05-20",
        "phone": "+224622111222",
        "schoolId": school_id,
    }
    # Premier essai sans force → 409
    res = await client.post("/api/census/teachers", json=payload, headers=headers)
    assert res.status_code == 409, res.text
    assert "duplicates" in res.json()["extra"]

    # Avec force=true → 201
    res2 = await client.post(
        "/api/census/teachers?force=true", json=payload, headers=headers
    )
    assert res2.status_code == 201, res2.text


# ===========================================================================
# RÉGRESSIONS MODULE 2.1 — review CRITICAL (5 issues bloquantes)
# ===========================================================================

# ---------------------------------------------------------------------------
# C-1a. Cas marketing : Aichatou vs Aïssatou SANS birth/phone → doit être
# au minimum MEDIUM (et idéalement HIGH grâce à la renormalisation).
# Avant fix : score=0.545 → LOW (faux négatif catastrophique).
# ---------------------------------------------------------------------------
def test_score_marketing_case_aichatou_vs_aissatou_without_birth_is_high_or_medium() -> None:
    a = {
        "firstName": "Aichatou",
        "lastName": "Dialo",
        "birthDate": None,
        "guardianPhone": None,
        "gender": "FEMALE",
        "schoolId": "X",
    }
    b = {
        "firstName": "Aïssatou",
        "lastName": "Diallo",
        "birthDate": None,
        "guardianPhone": None,
        "gender": "FEMALE",
        "schoolId": "X",
    }
    result = compute_similarity_score(a, b)
    cls = classify_score(result["score"])
    # Avant la correction de C-1, cls valait "LOW" (score 0.545). Après
    # renormalisation des poids actifs, on remonte autour de 0.9 → HIGH.
    assert cls in ("HIGH", "MEDIUM"), (
        f"score={result['score']} class={cls} — devrait être HIGH/MEDIUM"
    )


# ---------------------------------------------------------------------------
# C-1b. Poids renormalisés quand champs optionnels absents.
# ---------------------------------------------------------------------------
def test_score_weights_renormalize_when_optional_fields_absent() -> None:
    """``activeWeight`` doit retomber sur la somme des poids actifs uniquement."""
    full = {
        "firstName": "Mariam",
        "lastName": "Bah",
        "birthDate": date(2018, 6, 1),
        "guardianPhone": "+224622000111",
        "gender": "FEMALE",
        "schoolId": "school-1",
    }
    # Avec tous les champs → activeWeight = 1.0
    r_full = compute_similarity_score(full, dict(full))
    assert r_full["activeWeight"] == pytest.approx(1.0, abs=0.0001)
    assert r_full["score"] == pytest.approx(1.0, abs=0.0001)

    # Sans birthDate ni phone des deux côtés → activeWeight = 0.60
    no_birth_no_phone = dict(full, birthDate=None, guardianPhone=None)
    r_partial = compute_similarity_score(no_birth_no_phone, dict(no_birth_no_phone))
    assert r_partial["activeWeight"] == pytest.approx(0.60, abs=0.0001)
    # Score normalisé = 1.0 (match exact sur les 4 features actives)
    assert r_partial["score"] == pytest.approx(1.0, abs=0.0001)


# ---------------------------------------------------------------------------
# C-1c. Barrière exact-match legacy : même nom/prénom/birthDate dans la
# même école → 409 même quand le scoring fuzzy ne propose rien (e.g. parce
# que les noms sont parfaitement identiques avec aucune variation).
# ---------------------------------------------------------------------------
async def test_create_student_blocks_exact_duplicate_even_without_high_fuzzy(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, _ = await _make_school(db_session)
    # On place un student précisément normalisé (sans variante).
    await factories.StudentFactory.create_async(
        schoolId=school_id,
        firstName="Mariam",
        lastName="Bah",
        gender=Gender.FEMALE,
        birthDate=datetime(2018, 6, 1, tzinfo=UTC),
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    payload = {
        "firstName": "Mariam",
        "lastName": "Bah",
        "gender": "FEMALE",
        "birthDate": "2018-06-01",
        "schoolId": school_id,
    }
    res = await client.post("/api/census/students", json=payload, headers=headers)
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["code"] == "conflict"
    # La barrière EXACT remplit le payload duplicates pour cohérence client.
    assert "duplicates" in body["extra"]
    assert body["extra"]["duplicates"][0]["classification"] in ("EXACT", "HIGH")


# ---------------------------------------------------------------------------
# C-2. max_length anti-DoS sur les champs noms/prénoms.
# ---------------------------------------------------------------------------
async def test_create_student_rejects_oversized_name(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, _ = await _make_school(db_session)
    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    payload = {
        # 200 chars > max_length=120 → 422.
        "firstName": "A" * 200,
        "lastName": "Diallo",
        "gender": "FEMALE",
        "schoolId": school_id,
    }
    res = await client.post("/api/census/students", json=payload, headers=headers)
    assert res.status_code == 422, res.text
    # FastAPI/Pydantic décrit l'erreur dans `detail`.
    body = res.json()
    assert "detail" in body
    # On vérifie que la cause est bien la longueur (pour éviter qu'un autre
    # validateur fasse passer le test pour la mauvaise raison).
    assert any(
        "firstName" in str(loc) for err in body["detail"] for loc in err.get("loc", [])
    )


# ---------------------------------------------------------------------------
# C-3a. /check-duplicates est interdit aux TEACHER (énumération).
# ---------------------------------------------------------------------------
async def test_check_duplicates_blocks_teacher_role(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, _ = await _make_school(db_session)
    headers = await auth_headers(UserRole.TEACHER, schoolId=school_id)
    res = await client.post(
        "/api/census/students/check-duplicates",
        json={"firstName": "Aissatou", "lastName": "Diallo"},
        headers=headers,
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# C-3b. /check-duplicates ne doit pas renvoyer la birthDate exacte.
# ---------------------------------------------------------------------------
async def test_check_duplicates_response_does_not_expose_exact_birthdate(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, _ = await _make_school(db_session)
    # Birth précis qu'on veut s'assurer de NE PAS retrouver tel quel.
    secret_birth = datetime(2018, 3, 15, tzinfo=UTC)
    await factories.StudentFactory.create_async(
        schoolId=school_id,
        firstName="Aïssatou",
        lastName="Diallo",
        gender=Gender.FEMALE,
        birthDate=secret_birth,
        guardianPhone="+224622123456",
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    res = await client.post(
        "/api/census/students/check-duplicates",
        json={
            "firstName": "Aissatou",
            "lastName": "Diallo",
            "birthDate": "2018-03-15",
            "guardianPhone": "+224622123456",
            "schoolId": school_id,
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] >= 1
    match = body["matches"][0]
    # On ne renvoie PLUS la birthDate complète.
    assert "birthDate" not in match
    # On expose birthYear + un flag de cohérence — granularité acceptable.
    assert "birthYear" in match
    assert match["birthYear"] == 2018
    assert "birthDateMatches" in match
    assert match["birthDateMatches"] is True


# ---------------------------------------------------------------------------
# M-6a. create_student bloque sur incohérence âge/niveau classe.
# ---------------------------------------------------------------------------
async def test_create_student_blocks_birthdate_inconsistent_with_classroom(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, _ = await _make_school(db_session)
    # Crée une classe CM2 (attendu 9-11 ans à la rentrée).
    classroom = await factories.ClassRoomFactory.create_async(
        schoolId=school_id, level="CM2", name="CM2-A"
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    payload = {
        "firstName": "Petit",
        "lastName": "Bah",
        "gender": "MALE",
        # 4 ans aujourd'hui → trop jeune pour CM2 (attendu 9-11 ans).
        "birthDate": "2022-01-01",
        "schoolId": school_id,
        "classRoomId": classroom.id,
    }
    res = await client.post("/api/census/students", json=payload, headers=headers)
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["code"] == "validation_failed"
    assert "incohérente" in body["message"].lower() or "incoher" in body["message"].lower()
    assert body["extra"]["classRoomId"] == classroom.id


# ---------------------------------------------------------------------------
# M-6b. force=true sur incohérence d'âge → succès + audit log dédié.
# ---------------------------------------------------------------------------
async def test_create_student_allows_birthdate_inconsistency_with_force_and_audits(
    client: AsyncClient,
    db_session: AsyncSession,
    auth_headers,
) -> None:
    factories.bind(db_session)
    school_id, _ = await _make_school(db_session)
    classroom = await factories.ClassRoomFactory.create_async(
        schoolId=school_id, level="CM2", name="CM2-B"
    )

    headers = await auth_headers(UserRole.NATIONAL_ADMIN)
    payload = {
        "firstName": "Petite",
        "lastName": "Sow",
        "gender": "FEMALE",
        "birthDate": "2022-01-01",
        "schoolId": school_id,
        "classRoomId": classroom.id,
    }
    res = await client.post(
        "/api/census/students?force=true", json=payload, headers=headers
    )
    assert res.status_code == 201, res.text
    new_id = res.json()["id"]

    # Audit log dédié à l'override d'incohérence d'âge.
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == "OVERRIDE_BIRTHDATE_INCONSISTENT_WITH_CLASSROOM",
            )
        )
    ).scalars().all()
    assert len(audit) >= 1
    last = audit[-1]
    assert last.metadata_["classRoomId"] == classroom.id
    assert last.metadata_["level"] == "CM2"
    assert last.metadata_["birthDate"] == "2022-01-01"
    # Et le student a bien été créé.
    assert new_id is not None
