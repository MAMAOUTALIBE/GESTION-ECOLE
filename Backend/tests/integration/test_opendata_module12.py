"""Module 12 — Open Data portal (PUBLIC, sans auth).

Couvre :

1. Catalogue : 6 datasets, métadonnées + licence par défaut.
2. Format multiple : JSON (défaut) + CSV (téléchargement).
3. Endpoints publics : pas d'auth requise, 404 sur key inconnue.
4. Rate limit : 60 req/min/IP via Redis (fixed-window).
5. Anonymisation : hash IP déterministe, ne leak pas l'IP réelle,
   aucun PII dans les responses.
6. Audit : chaque téléchargement crée une OpendataDownload (ipHash).
7. Agrégations métier : dropout_risk, schools_by_region, stats.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.attendance.models import AttendanceRecord
from app.modules.diplomas.enums import DiplomaStatus, DiplomaType
from app.modules.diplomas.models import Diploma
from app.modules.opendata.anonymization import hash_ip, is_anonymous
from app.modules.opendata.datasets import DATASETS, get_dataset_spec
from app.modules.opendata.models import OpendataDataset, OpendataDownload
from app.modules.opendata.service import OpendataService
from app.modules.predictions.enums import DropoutRiskLevel
from app.modules.predictions.models import DropoutPrediction
from app.shared.base import generate_cuid
from app.shared.enums import AttendanceStatus, Gender, PersonType
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def opendata_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Crée un petit dataset pour les tests d'agrégation.

    Une région avec 2 écoles, quelques étudiants (1 M, 1 F), 1 enseignant,
    quelques observations d'attendance et 3 prédictions de risque
    (HIGH / MEDIUM / LOW).
    """
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    region = tree["region"]
    school = tree["school"]

    student_male = await factories.StudentFactory.create_async(
        schoolId=school.id, gender=Gender.MALE,
    )
    student_female = await factories.StudentFactory.create_async(
        schoolId=school.id, gender=Gender.FEMALE,
    )
    teacher = await factories.TeacherFactory.create_async(schoolId=school.id)

    # Attendance : 3 PRESENT, 1 ABSENT → taux = 0.75
    now = datetime.now(UTC)
    for status, who in [
        (AttendanceStatus.PRESENT, student_male.id),
        (AttendanceStatus.PRESENT, student_male.id),
        (AttendanceStatus.PRESENT, student_female.id),
        (AttendanceStatus.ABSENT, student_female.id),
    ]:
        db_session.add(
            AttendanceRecord(
                id=generate_cuid(),
                personType=PersonType.STUDENT,
                status=status,
                scannedAt=now,
                schoolId=school.id,
                studentId=who,
            )
        )

    # Prédictions de risque (Module 8) : 1 HIGH, 1 MEDIUM, 1 LOW
    for student, level, proba in [
        (student_male, DropoutRiskLevel.HIGH, 0.85),
        (student_female, DropoutRiskLevel.MEDIUM, 0.50),
    ]:
        db_session.add(
            DropoutPrediction(
                id=generate_cuid(),
                studentId=student.id,
                computedAt=now,
                probability=proba,
                riskLevel=level,
                featuresSnapshot={"feature_a": 1.0},
                modelVersion="test-v1",
            )
        )

    # Diplôme ISSUED 2026/CEPE (pour diplomas_issued_by_year)
    db_session.add(
        Diploma(
            id=generate_cuid(),
            serial="CEPE-2026-DEADBEEF",
            studentId=student_male.id,
            diplomaType=DiplomaType.CEPE,
            schoolId=school.id,
            status=DiplomaStatus.ISSUED,
            payloadSha256="0" * 64,
            signature="x" * 80,
            publicKeyFingerprint="a" * 32,
            issuedAt=now,
            signedAt=now,
        )
    )
    # Diplôme REVOKED 2026/BEPC — ne doit PAS être compté
    db_session.add(
        Diploma(
            id=generate_cuid(),
            serial="BEPC-2026-CAFEBABE",
            studentId=student_female.id,
            diplomaType=DiplomaType.BEPC,
            schoolId=school.id,
            status=DiplomaStatus.REVOKED,
            payloadSha256="1" * 64,
            signature="y" * 80,
            publicKeyFingerprint="b" * 32,
            issuedAt=now,
            signedAt=now,
            revokedAt=now,
            revokedReason="test",
        )
    )

    await db_session.flush()

    return {
        "region": region,
        "school": school,
        "student_male": student_male,
        "student_female": student_female,
        "teacher": teacher,
    }


# ===========================================================================
# 1. Catalogue
# ===========================================================================
@pytest.mark.asyncio
async def test_list_datasets_returns_six(client: AsyncClient) -> None:
    """Le catalogue MUST exposer EXACTEMENT 6 datasets MVP."""
    r = await client.get("/api/opendata/datasets")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 6
    keys = {item["key"] for item in body["items"]}
    assert keys == {
        "schools_by_region",
        "attendance_rate_by_region",
        "gender_distribution_by_region",
        "dropout_risk_by_region",
        "schools_density",
        "diplomas_issued_by_year",
    }


@pytest.mark.asyncio
async def test_get_dataset_metadata_includes_license(
    client: AsyncClient,
) -> None:
    """Chaque dataset doit avoir une licence CC-BY-4.0 + un schema JSON."""
    r = await client.get("/api/opendata/datasets/schools_by_region")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == "schools_by_region"
    assert body["license"] == "CC-BY-4.0"
    assert body["title"]
    assert body["description"]
    assert isinstance(body["schemaJsonb"], dict)
    assert body["schemaJsonb"]["type"] == "object"
    assert "regionName" in body["schemaJsonb"]["properties"]


# ===========================================================================
# 2. Format multiple (JSON + CSV)
# ===========================================================================
@pytest.mark.asyncio
async def test_get_dataset_data_json_format(
    client: AsyncClient, opendata_ctx: dict[str, Any],
) -> None:
    r = await client.get(
        "/api/opendata/datasets/schools_by_region/data?format=json",
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    data = r.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    # Notre région doit y figurer (créée par opendata_ctx)
    region_names = {row["regionName"] for row in data}
    assert opendata_ctx["region"].name in region_names


@pytest.mark.asyncio
async def test_get_dataset_data_csv_format(
    client: AsyncClient, opendata_ctx: dict[str, Any],
) -> None:
    r = await client.get(
        "/api/opendata/datasets/schools_by_region/data?format=csv",
    )
    assert r.status_code == 200, r.text
    body_text = r.text
    # Header CSV présent (BOM Excel possible, on strip pour parser)
    reader = csv.DictReader(io.StringIO(body_text.lstrip("﻿")))
    rows = list(reader)
    assert len(rows) >= 1
    assert set(reader.fieldnames or []) == {
        "regionName", "schoolCount", "studentCount", "teacherCount",
    }


@pytest.mark.asyncio
async def test_csv_download_has_correct_content_type(
    client: AsyncClient, opendata_ctx: dict[str, Any],
) -> None:
    r = await client.get(
        "/api/opendata/datasets/schools_by_region/data?format=csv",
    )
    assert r.status_code == 200, r.text
    assert "text/csv" in r.headers["content-type"]
    # Content-Disposition pour déclencher le download navigateur.
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "schools_by_region.csv" in r.headers["content-disposition"]


# ===========================================================================
# 3. Endpoints publics + 404
# ===========================================================================
@pytest.mark.asyncio
async def test_datasets_endpoints_are_public_no_auth_needed(
    client: AsyncClient,
) -> None:
    """Aucun header Authorization → réponse 200 sur tous les endpoints."""
    for path in [
        "/api/opendata/datasets",
        "/api/opendata/datasets/schools_by_region",
        "/api/opendata/datasets/schools_by_region/data",
        "/api/opendata/stats",
    ]:
        r = await client.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code} {r.text}"


@pytest.mark.asyncio
async def test_unknown_dataset_returns_404(client: AsyncClient) -> None:
    r = await client.get("/api/opendata/datasets/does_not_exist")
    assert r.status_code == 404, r.text
    r2 = await client.get("/api/opendata/datasets/does_not_exist/data")
    assert r2.status_code == 404, r2.text


# ===========================================================================
# 4. Rate limit (60 req/min/IP)
# ===========================================================================
@pytest.mark.asyncio
async def test_rate_limit_blocks_after_60_per_minute(
    client: AsyncClient,
) -> None:
    """La 61e requête depuis la même IP renvoie HTTP 429.

    On vise un endpoint léger (``/datasets``) pour ne pas exécuter 60 fois
    une agrégation coûteuse. Redis DB 15 est flush entre chaque test.
    """
    # Les 60 premières doivent passer
    for i in range(60):
        r = await client.get("/api/opendata/datasets")
        assert r.status_code == 200, (
            f"req #{i + 1} should pass, got {r.status_code}"
        )
    # La 61e doit être refusée
    r = await client.get("/api/opendata/datasets")
    assert r.status_code == 429, r.text
    body = r.json()
    assert body["code"] == "rate_limited"


# ===========================================================================
# 5. Anonymisation (hash IP + no PII)
# ===========================================================================
def test_ip_hash_is_deterministic_and_does_not_leak_ip() -> None:
    """``hash_ip`` doit être déterministe et ne PAS exposer l'IP source."""
    h1 = hash_ip("203.0.113.42")
    h2 = hash_ip("203.0.113.42")
    h3 = hash_ip("198.51.100.1")
    assert h1 == h2, "même IP → même hash (déterministe)"
    assert h1 != h3, "IPs différentes → hashes différents"
    assert len(h1) == 64, "SHA-256 hex = 64 chars"
    # L'IP source ne doit JAMAIS apparaître dans le hash (en tout cas pas
    # en clair). On vérifie qu'aucun substring de l'IP n'est dans le hash.
    assert "203" not in h1
    assert "113" not in h1
    assert "0.0" not in h1


@pytest.mark.asyncio
async def test_anonymization_no_pii_in_response(
    client: AsyncClient, opendata_ctx: dict[str, Any],
) -> None:
    """Aucun record exposé par les 6 datasets ne doit contenir un PII.

    Le garde-fou :func:`is_anonymous` vérifie que les NOMS de champs ne
    ressemblent pas à un PII (firstName, studentId, phone…). On parcourt
    les 6 datasets et on s'assure que tous les records passent.
    """
    for spec in DATASETS:
        r = await client.get(
            f"/api/opendata/datasets/{spec.key}/data?format=json",
        )
        assert r.status_code == 200, (
            f"{spec.key} → {r.status_code} {r.text}"
        )
        data = r.json()
        assert isinstance(data, list)
        for record in data:
            assert is_anonymous(record), (
                f"Dataset {spec.key} fuite un PII : {record!r}"
            )
        # En complément : aucun ID interne (cuid 25 chars) ne doit
        # apparaître dans le payload brut.
        raw = r.text
        for cuid in [
            opendata_ctx["student_male"].id,
            opendata_ctx["student_female"].id,
            opendata_ctx["school"].id,
            opendata_ctx["teacher"].id,
        ]:
            assert cuid not in raw, (
                f"Dataset {spec.key} leak l'ID interne {cuid!r}"
            )


# ===========================================================================
# 6. Audit anonyme (OpendataDownload)
# ===========================================================================
@pytest.mark.asyncio
async def test_download_logged_with_ip_hash(
    client: AsyncClient,
    db_session: AsyncSession,
    opendata_ctx: dict[str, Any],
) -> None:
    """Un téléchargement crée une OpendataDownload avec un ipHash, jamais l'IP."""
    r = await client.get(
        "/api/opendata/datasets/schools_by_region/data?format=json",
    )
    assert r.status_code == 200, r.text

    rows = (await db_session.execute(
        select(OpendataDownload).where(
            OpendataDownload.datasetKey == "schools_by_region",
        ),
    )).scalars().all()

    assert len(rows) >= 1, "au moins une ligne d'audit doit être créée"
    entry = rows[-1]
    assert entry.format == "json"
    assert len(entry.ipHash) == 64, "ipHash doit être un SHA-256 hex"
    # L'IP littérale du test (httpx → 127.0.0.1 / testserver) ne doit JAMAIS
    # apparaître dans la colonne ipHash.
    assert "127.0.0.1" not in entry.ipHash
    assert "test" not in entry.ipHash.lower()


# ===========================================================================
# 7. Agrégations métier (datasets clés)
# ===========================================================================
@pytest.mark.asyncio
async def test_schools_by_region_dataset_aggregates(
    client: AsyncClient, opendata_ctx: dict[str, Any],
) -> None:
    """Le dataset doit renvoyer les bons compteurs pour la région créée."""
    r = await client.get(
        "/api/opendata/datasets/schools_by_region/data?format=json",
    )
    assert r.status_code == 200, r.text
    data = r.json()
    our_region = next(
        (row for row in data
         if row["regionName"] == opendata_ctx["region"].name),
        None,
    )
    assert our_region is not None, "notre région doit figurer"
    assert our_region["schoolCount"] >= 1
    assert our_region["studentCount"] >= 2
    assert our_region["teacherCount"] >= 1


@pytest.mark.asyncio
async def test_dropout_risk_dataset_aggregates_correctly(
    client: AsyncClient, opendata_ctx: dict[str, Any],
) -> None:
    """Dataset dropout_risk_by_region — comptage HIGH/MEDIUM/LOW correct."""
    r = await client.get(
        "/api/opendata/datasets/dropout_risk_by_region/data?format=json",
    )
    assert r.status_code == 200, r.text
    data = r.json()
    our_region = next(
        (row for row in data
         if row["regionName"] == opendata_ctx["region"].name),
        None,
    )
    assert our_region is not None
    # On a inséré 1 HIGH (student_male) + 1 MEDIUM (student_female).
    assert our_region["highRiskCount"] >= 1
    assert our_region["mediumRiskCount"] >= 1
    # Schema check : 3 colonnes attendues
    assert set(our_region.keys()) == {
        "regionName", "highRiskCount", "mediumRiskCount", "lowRiskCount",
    }


@pytest.mark.asyncio
async def test_stats_endpoint_returns_download_counts(
    client: AsyncClient,
    db_session: AsyncSession,
    opendata_ctx: dict[str, Any],
) -> None:
    """``/stats`` doit refléter les téléchargements précédents."""
    # On déclenche 2 téléchargements (JSON + CSV) pour avoir des compteurs > 0.
    await client.get(
        "/api/opendata/datasets/schools_by_region/data?format=json",
    )
    await client.get(
        "/api/opendata/datasets/attendance_rate_by_region/data?format=csv",
    )

    r = await client.get("/api/opendata/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totalDownloads"] >= 2
    assert body["downloadsByDataset"]["schools_by_region"] >= 1
    assert body["downloadsByDataset"]["attendance_rate_by_region"] >= 1
    assert body["downloadsByFormat"]["json"] >= 1
    assert body["downloadsByFormat"]["csv"] >= 1


# ===========================================================================
# 8. Bonus — couvertures complémentaires
# ===========================================================================
@pytest.mark.asyncio
async def test_diplomas_issued_dataset_excludes_revoked(
    client: AsyncClient, opendata_ctx: dict[str, Any],
) -> None:
    """Un diplôme REVOKED ne doit PAS apparaître dans le comptage public."""
    r = await client.get(
        "/api/opendata/datasets/diplomas_issued_by_year/data?format=json",
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # Le CEPE-2026 ISSUED doit être compté (>=1), pas le BEPC-2026 REVOKED.
    cepe = [row for row in data
            if row["year"] == 2026 and row["diplomaType"] == "CEPE"]
    bepc = [row for row in data
            if row["year"] == 2026 and row["diplomaType"] == "BEPC"]
    assert len(cepe) >= 1
    assert cepe[0]["count"] >= 1
    # BEPC REVOKED → on ne doit PAS le voir.
    assert bepc == [], (
        "le BEPC REVOKED ne doit pas être compté côté public"
    )


@pytest.mark.asyncio
async def test_refresh_dataset_metadata_persists_record_count(
    db_session: AsyncSession, opendata_ctx: dict[str, Any],
) -> None:
    """``refresh_dataset_metadata`` upsert ``recordCount`` + ``lastRefreshedAt``."""
    svc = OpendataService(db_session)
    row = await svc.refresh_dataset_metadata("schools_by_region")
    assert row is not None
    assert row.key == "schools_by_region"
    assert row.recordCount is not None and row.recordCount >= 1
    assert row.lastRefreshedAt is not None

    # Re-appel : update du même row (pas d'insert dupliqué).
    row2 = await svc.refresh_dataset_metadata("schools_by_region")
    assert row2 is not None
    assert row2.id == row.id

    # Unknown key → None, pas de crash.
    assert (
        await svc.refresh_dataset_metadata("does_not_exist") is None
    )
