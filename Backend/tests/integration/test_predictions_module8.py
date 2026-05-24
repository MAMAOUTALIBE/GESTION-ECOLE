"""Module 8 — Predictions ML (détection précoce du décrochage).

Couvre :
1. Features extraction (avec données / sans données)
2. Synthetic training set (shape, label balance)
3. Modèle (accuracy raisonnable, predict_proba range, seuils riskLevel)
4. PredictionService (persistance, batch, listing)
5. Router (RBAC, 404, model/train, model/info, featuresSnapshot)
"""
from __future__ import annotations

import os
import tempfile
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.attendance.models import AttendanceRecord
from app.modules.predictions.enums import DropoutRiskLevel
from app.modules.predictions.features import (
    FEATURE_DEFAULTS,
    FEATURE_NAMES,
    extract_features,
)
from app.modules.predictions.model import (
    RISK_HIGH_MIN,
    RISK_LOW_MAX,
    DropoutModel,
    risk_level_for_proba,
    train,
)
from app.modules.predictions.models import DropoutModelMetadata, DropoutPrediction
from app.modules.predictions.service import PredictionService, _reset_model_cache
from app.modules.predictions.training import (
    DEFAULT_ARTIFACT_PATH,
    _next_version,
    generate_synthetic_training_set,
    train_initial_model_task,
)
from app.shared.base import generate_cuid
from app.shared.enums import AttendanceStatus, PersonType, UserRole
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def school_ctx(db_session: AsyncSession) -> dict[str, Any]:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    students = []
    for _ in range(3):
        s = await factories.StudentFactory.create_async(schoolId=tree["school"].id)
        students.append(s)
    return {
        "region": tree["region"],
        "prefecture": tree["prefecture"],
        "subPrefecture": tree["subPrefecture"],
        "school": tree["school"],
        "students": students,
    }


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
async def teacher_headers(
    auth_headers: Any, school_ctx: dict[str, Any],
) -> dict[str, str]:
    return await auth_headers(
        UserRole.TEACHER,
        regionId=school_ctx["region"].id,
        schoolId=school_ctx["school"].id,
    )


@pytest_asyncio.fixture(loop_scope="session")
async def national_headers(auth_headers: Any) -> dict[str, str]:
    return await auth_headers(UserRole.NATIONAL_ADMIN)


@pytest_asyncio.fixture()
def tmp_artifact_path():
    """Chemin temporaire unique pour le joblib de chaque test."""
    fd, path = tempfile.mkstemp(suffix=".joblib", prefix="dropout-model-test-")
    os.close(fd)
    # On supprime le fichier vide créé par mkstemp ; le test l'écrira.
    if os.path.exists(path):
        os.remove(path)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest_asyncio.fixture(autouse=True)
def _isolate_model_cache():
    """Vide le cache process-level avant CHAQUE test."""
    _reset_model_cache()
    yield
    _reset_model_cache()


async def _seed_trained_model(
    session: AsyncSession, artifact_path: str = DEFAULT_ARTIFACT_PATH,
) -> str:
    """Helper : entraîne et persiste un modèle pour les tests qui en ont besoin."""
    version = await train_initial_model_task(
        session, artifact_path=artifact_path, n_samples=500,
    )
    return version


# ===========================================================================
# 1. Features
# ===========================================================================
@pytest.mark.asyncio
async def test_extract_features_returns_six_features_for_existing_student(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    student = school_ctx["students"][0]
    # Seed quelques scans présence
    now = datetime.now(UTC)
    for i in range(10):
        rec = AttendanceRecord(
            id=generate_cuid(),
            personType=PersonType.STUDENT,
            status=AttendanceStatus.PRESENT,
            scannedAt=now - timedelta(days=i),
            schoolId=school_ctx["school"].id,
            studentId=student.id,
        )
        db_session.add(rec)
    await db_session.flush()

    features = await extract_features(db_session, student.id, date.today())
    assert set(features.keys()) == set(FEATURE_NAMES)
    assert len(features) == 6
    # Avec 10 PRESENT et 0 absent, attendance_rate_30d == 1.0
    assert features["attendance_rate_30d"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_extract_features_handles_student_with_no_grades(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    """Élève sans aucune donnée — toutes les features doivent prendre les defaults."""
    factories.bind(db_session)
    new_student = await factories.StudentFactory.create_async(
        schoolId=school_ctx["school"].id,
    )
    features = await extract_features(db_session, new_student.id, date.today())
    assert features["attendance_rate_90d"] == FEATURE_DEFAULTS["attendance_rate_90d"]
    assert features["attendance_rate_30d"] == FEATURE_DEFAULTS["attendance_rate_30d"]
    assert features["grade_avg_last_period"] == FEATURE_DEFAULTS["grade_avg_last_period"]
    assert features["grade_trend"] == FEATURE_DEFAULTS["grade_trend"]
    assert features["incidents_count_180d"] == 0.0
    assert features["late_count_30d"] == 0.0


# ===========================================================================
# 2. Synthetic training set
# ===========================================================================
def test_synthetic_training_set_generates_correct_shape() -> None:
    X, y = generate_synthetic_training_set(n_samples=1000)
    assert X.shape == (1000, len(FEATURE_NAMES))
    assert y.shape == (1000,)
    # Les deux classes doivent exister (sinon le modèle dégénère)
    assert set(np.unique(y).tolist()) == {0, 1}
    # Le label positif ne doit être ni 0% ni 100% des cas
    rate = y.mean()
    assert 0.01 < rate < 0.50, f"Taux positif déséquilibré: {rate}"


# ===========================================================================
# 3. Model
# ===========================================================================
def test_train_returns_model_with_reasonable_accuracy() -> None:
    X, y = generate_synthetic_training_set(n_samples=2000)
    model, metrics = train(X, y, version="v-test")
    assert isinstance(model, DropoutModel)
    assert model.version == "v-test"
    # Sur données synthétiques quasi-déterministes, la logistic regression
    # doit dépasser largement 0.7 d'accuracy.
    assert metrics["accuracy"] > 0.7, f"Accuracy trop basse: {metrics['accuracy']}"


def test_predict_proba_returns_value_in_0_1_range() -> None:
    X, y = generate_synthetic_training_set(n_samples=500)
    model, _ = train(X, y, version="v-range")
    # Génère un dict features ARBITRAIRE
    features = dict.fromkeys(FEATURE_NAMES, 5.0)
    features["attendance_rate_30d"] = 0.5
    features["attendance_rate_90d"] = 0.5
    proba, level = model.predict_proba(features)
    assert 0.0 <= proba <= 1.0
    assert isinstance(level, DropoutRiskLevel)


def test_risk_level_thresholds_correct() -> None:
    assert risk_level_for_proba(0.0) == DropoutRiskLevel.LOW
    assert risk_level_for_proba(RISK_LOW_MAX - 0.01) == DropoutRiskLevel.LOW
    assert risk_level_for_proba(RISK_LOW_MAX) == DropoutRiskLevel.MEDIUM
    assert risk_level_for_proba(0.50) == DropoutRiskLevel.MEDIUM
    assert risk_level_for_proba(RISK_HIGH_MIN) == DropoutRiskLevel.MEDIUM
    assert risk_level_for_proba(RISK_HIGH_MIN + 0.01) == DropoutRiskLevel.HIGH
    assert risk_level_for_proba(1.0) == DropoutRiskLevel.HIGH


def test_model_save_and_load_roundtrip(tmp_artifact_path: str) -> None:
    X, y = generate_synthetic_training_set(n_samples=200)
    model, _ = train(X, y, version="v-roundtrip")
    model.save(tmp_artifact_path)
    assert os.path.exists(tmp_artifact_path)
    loaded = DropoutModel.load(tmp_artifact_path)
    assert loaded.version == "v-roundtrip"
    assert loaded.feature_names == FEATURE_NAMES


# ===========================================================================
# 4. PredictionService — persistance, batch, listing
# ===========================================================================
@pytest.mark.asyncio
async def test_predict_student_persists_in_db(
    db_session: AsyncSession, school_ctx: dict[str, Any], tmp_artifact_path: str,
) -> None:
    factories.bind(db_session)
    await _seed_trained_model(db_session, artifact_path=tmp_artifact_path)
    student = school_ctx["students"][0]

    service = PredictionService(db_session)
    prediction = await service.predict_student(student.id)

    assert prediction.id is not None
    assert prediction.studentId == student.id
    assert 0.0 <= prediction.probability <= 1.0
    assert prediction.riskLevel in {
        DropoutRiskLevel.LOW, DropoutRiskLevel.MEDIUM, DropoutRiskLevel.HIGH,
    }
    # Vérif persistance
    stmt = select(DropoutPrediction).where(DropoutPrediction.id == prediction.id)
    row = (await db_session.execute(stmt)).scalar_one()
    assert row.modelVersion == prediction.modelVersion


@pytest.mark.asyncio
async def test_batch_predict_school_returns_count(
    db_session: AsyncSession, school_ctx: dict[str, Any], tmp_artifact_path: str,
) -> None:
    factories.bind(db_session)
    await _seed_trained_model(db_session, artifact_path=tmp_artifact_path)

    service = PredictionService(db_session)
    count = await service.batch_predict_school(school_ctx["school"].id)
    # school_ctx a 3 élèves
    assert count == 3


@pytest.mark.asyncio
async def test_list_at_risk_returns_high_level_only(
    db_session: AsyncSession, school_ctx: dict[str, Any], tmp_artifact_path: str,
) -> None:
    factories.bind(db_session)
    await _seed_trained_model(db_session, artifact_path=tmp_artifact_path)
    school = school_ctx["school"]
    students = school_ctx["students"]
    # Seed des prédictions manuelles à 3 niveaux
    now = datetime.now(UTC)
    for stu, level, proba in (
        (students[0], DropoutRiskLevel.LOW, 0.10),
        (students[1], DropoutRiskLevel.MEDIUM, 0.45),
        (students[2], DropoutRiskLevel.HIGH, 0.85),
    ):
        db_session.add(DropoutPrediction(
            id=generate_cuid(), studentId=stu.id, schoolYearId=None,
            computedAt=now, probability=proba, riskLevel=level,
            featuresSnapshot={}, modelVersion="seed",
        ))
    await db_session.flush()

    service = PredictionService(db_session)
    high_only = await service.list_at_risk(
        school.id, level=DropoutRiskLevel.HIGH, limit=50,
    )
    assert len(high_only) == 1
    assert high_only[0].studentId == students[2].id
    assert high_only[0].riskLevel == DropoutRiskLevel.HIGH


# ===========================================================================
# 5. Router — RBAC, 404, model endpoints, snapshot
# ===========================================================================
@pytest.mark.asyncio
async def test_rbac_predict_requires_director(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], teacher_headers: dict[str, str],
    tmp_artifact_path: str,
) -> None:
    factories.bind(db_session)
    await _seed_trained_model(db_session, artifact_path=tmp_artifact_path)
    student = school_ctx["students"][0]

    r = await client.post(
        f"/api/predictions/students/{student.id}/predict",
        headers=teacher_headers,
    )
    # TEACHER n'est PAS dans SCORING_ROLES → 403
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_predict_endpoint_returns_404_for_unknown_student(
    client: AsyncClient, db_session: AsyncSession,
    director_headers: dict[str, str], tmp_artifact_path: str,
) -> None:
    await _seed_trained_model(db_session, artifact_path=tmp_artifact_path)
    r = await client.post(
        "/api/predictions/students/nonexistent-id-12345/predict",
        headers=director_headers,
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_model_train_endpoint_requires_national_admin(
    client: AsyncClient, director_headers: dict[str, str],
) -> None:
    r = await client.post(
        "/api/predictions/model/train", headers=director_headers,
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_model_info_returns_metadata(
    client: AsyncClient, db_session: AsyncSession,
    director_headers: dict[str, str], tmp_artifact_path: str,
) -> None:
    version = await _seed_trained_model(db_session, artifact_path=tmp_artifact_path)
    r = await client.get(
        "/api/predictions/model/info", headers=director_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == version
    assert body["loaded"] is True
    assert "accuracy" in body["metrics"]


@pytest.mark.asyncio
async def test_features_snapshot_stored_in_prediction_row(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
    tmp_artifact_path: str,
) -> None:
    await _seed_trained_model(db_session, artifact_path=tmp_artifact_path)
    student = school_ctx["students"][0]

    r = await client.post(
        f"/api/predictions/students/{student.id}/predict",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "featuresSnapshot" in body
    snap = body["featuresSnapshot"]
    assert set(snap.keys()) == set(FEATURE_NAMES)
    # Round-trip DB
    stmt = select(DropoutPrediction).where(DropoutPrediction.id == body["id"])
    row = (await db_session.execute(stmt)).scalar_one()
    assert set(row.featuresSnapshot.keys()) == set(FEATURE_NAMES)


# ===========================================================================
# Sanity — version generator est unique
# ===========================================================================
def test_next_version_format() -> None:
    v = _next_version()
    assert v.startswith("v1-")
    assert len(v) <= 20  # tient dans la colonne VARCHAR(20)
