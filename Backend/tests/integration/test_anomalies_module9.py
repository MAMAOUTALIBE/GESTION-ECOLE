"""Module 9 — Anomalies detection (rule-based + workflow human-in-the-loop).

Couvre :
1. Détecteurs individuels (impossible grades, attendance 100%, grade jump,
   late birthdate, duplicate codes, excessive transfers)
2. AnomalyService.run_all_detectors (persistance)
3. AnomalyService.list / review / stats
4. Router : RBAC, scope territorial, JSONB evidence
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import (
    AcademicPeriod,
    Assessment,
    Grade,
    ReportCard,
    SchoolYear,
    Subject,
)
from app.modules.anomalies.detectors import (
    detect_duplicate_codes,
    detect_excessive_transfers,
    detect_grade_jump,
    detect_impossible_grades,
    detect_late_birthdate,
    detect_suspicious_attendance_100,
)
from app.modules.anomalies.enums import (
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
)
from app.modules.anomalies.models import AnomalyDetection
from app.modules.anomalies.service import AnomalyService
from app.modules.attendance.models import AttendanceRecord
from app.modules.census.models import Student, StudentTransfer
from app.shared.base import generate_cuid
from app.shared.enums import (
    AcademicPeriodType,
    AcademicValidationStatus,
    AssessmentType,
    AttendanceStatus,
    Gender,
    PersonType,
    UserRole,
)
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures de base
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
async def regional_headers(
    auth_headers: Any, school_ctx: dict[str, Any],
) -> dict[str, str]:
    return await auth_headers(
        UserRole.REGIONAL_ADMIN,
        regionId=school_ctx["region"].id,
    )


@pytest_asyncio.fixture(loop_scope="session")
async def national_headers(auth_headers: Any) -> dict[str, str]:
    return await auth_headers(UserRole.NATIONAL_ADMIN)


# ---------------------------------------------------------------------------
# Helpers : créer un mini contexte académique (year + period + subject + class)
# pour les tests qui ont besoin de Grades / ReportCards.
# ---------------------------------------------------------------------------
async def _academic_ctx(
    session: AsyncSession, school_id: str,
) -> dict[str, Any]:
    factories.bind(session)
    classroom = await factories.ClassRoomFactory.create_async(schoolId=school_id)
    year = SchoolYear(
        id=generate_cuid(),
        name=f"YEAR-{generate_cuid()[:6]}",
        startDate=datetime(2025, 9, 1, tzinfo=UTC),
        endDate=datetime(2026, 6, 30, tzinfo=UTC),
        periodType=AcademicPeriodType.TRIMESTER,
        isActive=True,
    )
    session.add(year)
    period_a = AcademicPeriod(
        id=generate_cuid(), name="T1",
        type=AcademicPeriodType.TRIMESTER, order=1,
        schoolYearId=year.id,
    )
    period_b = AcademicPeriod(
        id=generate_cuid(), name="T2",
        type=AcademicPeriodType.TRIMESTER, order=2,
        schoolYearId=year.id,
    )
    session.add(period_a)
    session.add(period_b)
    subject = Subject(
        id=generate_cuid(), code=f"S-{generate_cuid()[:6]}",
        name="Mathematiques", coefficient=1.0,
    )
    session.add(subject)
    await session.flush()
    assessment = Assessment(
        id=generate_cuid(), title="Quiz #1",
        type=AssessmentType.QUIZ, coefficient=1.0, maxScore=20.0,
        schoolYearId=year.id, periodId=period_a.id,
        subjectId=subject.id, classRoomId=classroom.id,
        status=AcademicValidationStatus.VALIDATED,
    )
    session.add(assessment)
    await session.flush()
    return {
        "classroom": classroom,
        "year": year,
        "periodA": period_a,
        "periodB": period_b,
        "subject": subject,
        "assessment": assessment,
    }


def _make_grade(
    *, assessment_id: str, student_id: str, year_id: str, period_id: str,
    subject_id: str, classroom_id: str, score: float,
) -> Grade:
    now = datetime.now(UTC)
    return Grade(
        id=generate_cuid(),
        assessmentId=assessment_id,
        studentId=student_id,
        schoolYearId=year_id,
        periodId=period_id,
        subjectId=subject_id,
        classRoomId=classroom_id,
        score=score,
        status=AcademicValidationStatus.VALIDATED,
        recordedAt=now,
        updatedAt=now,
    )


# ===========================================================================
# 1. Détecteurs unitaires
# ===========================================================================
@pytest.mark.asyncio
async def test_detect_impossible_grades_flags_negative_score(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    acad = await _academic_ctx(db_session, school_ctx["school"].id)
    student = school_ctx["students"][0]
    db_session.add(_make_grade(
        assessment_id=acad["assessment"].id,
        student_id=student.id,
        year_id=acad["year"].id,
        period_id=acad["periodA"].id,
        subject_id=acad["subject"].id,
        classroom_id=acad["classroom"].id,
        score=-5.0,
    ))
    await db_session.flush()

    results = await detect_impossible_grades(db_session)
    assert len(results) == 1
    anomaly = results[0]
    assert anomaly.type == AnomalyType.IMPOSSIBLE_GRADE
    assert anomaly.severity == AnomalySeverity.CRITICAL
    assert anomaly.evidence["score"] == -5.0


@pytest.mark.asyncio
async def test_detect_impossible_grades_flags_over_20(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    acad = await _academic_ctx(db_session, school_ctx["school"].id)
    student = school_ctx["students"][0]
    db_session.add(_make_grade(
        assessment_id=acad["assessment"].id,
        student_id=student.id,
        year_id=acad["year"].id,
        period_id=acad["periodA"].id,
        subject_id=acad["subject"].id,
        classroom_id=acad["classroom"].id,
        score=25.0,
    ))
    # Un grade valide ne devrait PAS être flaggé
    db_session.add(_make_grade(
        assessment_id=acad["assessment"].id,
        student_id=school_ctx["students"][1].id,
        year_id=acad["year"].id,
        period_id=acad["periodA"].id,
        subject_id=acad["subject"].id,
        classroom_id=acad["classroom"].id,
        score=15.0,
    ))
    await db_session.flush()

    results = await detect_impossible_grades(db_session)
    assert len(results) == 1
    assert results[0].evidence["score"] == 25.0


@pytest.mark.asyncio
async def test_detect_suspicious_attendance_100_pct_over_60_days(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    student = school_ctx["students"][0]
    school = school_ctx["school"]
    # 65 jours PRESENT consécutifs, aucun ABSENT
    base = datetime.now(UTC) - timedelta(days=65)
    for i in range(65):
        rec = AttendanceRecord(
            id=generate_cuid(),
            personType=PersonType.STUDENT,
            status=AttendanceStatus.PRESENT,
            scannedAt=base + timedelta(days=i),
            schoolId=school.id,
            studentId=student.id,
        )
        db_session.add(rec)
    await db_session.flush()

    results = await detect_suspicious_attendance_100(db_session, school_id=school.id)
    assert len(results) == 1
    assert results[0].type == AnomalyType.SUSPICIOUS_ATTENDANCE
    assert results[0].entityId == student.id
    assert results[0].evidence["daysPresent"] >= 60


@pytest.mark.asyncio
async def test_detect_grade_jump_more_than_8_points(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    acad = await _academic_ctx(db_session, school_ctx["school"].id)
    student = school_ctx["students"][0]
    # ReportCard T1 = 8.0, T2 = 18.0 → delta = +10 (au-dessus du seuil 8)
    db_session.add(ReportCard(
        id=generate_cuid(), studentId=student.id, classRoomId=acad["classroom"].id,
        schoolYearId=acad["year"].id, periodId=acad["periodA"].id,
        average=8.0, rank=None, totalStudents=None,
        verificationCode=f"VC-{generate_cuid()[:10]}",
    ))
    db_session.add(ReportCard(
        id=generate_cuid(), studentId=student.id, classRoomId=acad["classroom"].id,
        schoolYearId=acad["year"].id, periodId=acad["periodB"].id,
        average=18.0, rank=None, totalStudents=None,
        verificationCode=f"VC-{generate_cuid()[:10]}",
    ))
    await db_session.flush()

    results = await detect_grade_jump(db_session)
    matching = [a for a in results if a.entityId == student.id]
    assert len(matching) == 1
    ev = matching[0].evidence
    assert ev["previousAverage"] == 8.0
    assert ev["currentAverage"] == 18.0
    assert ev["delta"] == 10.0


@pytest.mark.asyncio
async def test_detect_late_birthdate(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    # On crée un élève avec une date de naissance dans le futur
    future = datetime.now(UTC) + timedelta(days=365 * 5)
    stu = await factories.StudentFactory.create_async(
        schoolId=school_ctx["school"].id,
        firstName="Future",
        lastName="Birth",
        birthDate=future,
    )
    await db_session.flush()

    results = await detect_late_birthdate(db_session)
    matching = [a for a in results if a.entityId == stu.id]
    assert len(matching) == 1
    assert matching[0].type == AnomalyType.INVALID_BIRTHDATE
    assert matching[0].severity == AnomalySeverity.CRITICAL


@pytest.mark.asyncio
async def test_detect_duplicate_codes(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    """Pour ce test on contourne la contrainte UNIQUE en insérant deux Student
    en utilisant la même valeur unique_code via raw SQL après avoir désactivé
    temporairement la contrainte (test postgres).

    En pratique, comme l'unicité est portée par PostgreSQL, on ne peut PAS
    insérer deux rows directement. Notre détecteur reste un audit : on
    vérifie ici qu'il ne crashe PAS et ne lève AUCUNE anomalie quand la
    contrainte tient. (Le détecteur est conçu pour réagir si la contrainte
    saute en production, mais tester ce cas exigerait de drop la contrainte.)
    """
    factories.bind(db_session)
    # Crée 2 élèves avec des codes différents — doit retourner [].
    await factories.StudentFactory.create_async(
        schoolId=school_ctx["school"].id,
    )
    await factories.StudentFactory.create_async(
        schoolId=school_ctx["school"].id,
    )
    await db_session.flush()

    results = await detect_duplicate_codes(db_session)
    # Aucun doublon attendu (la contrainte UNIQUE tient).
    assert results == []


@pytest.mark.asyncio
async def test_detect_excessive_transfers_more_than_3_per_year(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    student = school_ctx["students"][0]
    school_a = school_ctx["school"]
    # Crée une 2e école pour la cible des transferts
    other = await factories.SchoolFactory.create_async(
        regionId=school_ctx["region"].id,
        prefectureId=school_ctx["prefecture"].id,
        subPrefectureId=school_ctx["subPrefecture"].id,
    )
    now = datetime.now(UTC)
    # 4 transferts en 6 mois : > 3 → anomalie
    for i in range(4):
        db_session.add(StudentTransfer(
            id=generate_cuid(),
            studentId=student.id,
            fromSchoolId=school_a.id,
            toSchoolId=other.id,
            transferredAt=now - timedelta(days=30 * (i + 1)),
        ))
    await db_session.flush()

    results = await detect_excessive_transfers(db_session)
    matching = [a for a in results if a.entityId == student.id]
    assert len(matching) == 1
    assert matching[0].type == AnomalyType.EXCESSIVE_TRANSFER
    assert matching[0].evidence["transferCount"] == 4


# ===========================================================================
# 2. AnomalyService — persistance & listing
# ===========================================================================
@pytest.mark.asyncio
async def test_run_all_detectors_persists_results(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    acad = await _academic_ctx(db_session, school_ctx["school"].id)
    student = school_ctx["students"][0]
    # Une note impossible (score 25) → 1 anomalie CRITICAL
    db_session.add(_make_grade(
        assessment_id=acad["assessment"].id,
        student_id=student.id,
        year_id=acad["year"].id,
        period_id=acad["periodA"].id,
        subject_id=acad["subject"].id,
        classroom_id=acad["classroom"].id,
        score=25.0,
    ))
    await db_session.flush()

    service = AnomalyService(db_session)
    count = await service.run_all_detectors()
    assert count >= 1

    # Vérif persistance : on doit trouver au moins l'anomalie IMPOSSIBLE_GRADE
    stmt = select(AnomalyDetection).where(
        AnomalyDetection.type == AnomalyType.IMPOSSIBLE_GRADE,
    )
    rows = list((await db_session.execute(stmt)).scalars())
    assert len(rows) >= 1
    assert rows[0].status == AnomalyStatus.PENDING


@pytest.mark.asyncio
async def test_list_anomalies_filters_by_status(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    now = datetime.now(UTC)
    # Seed manuel : 2 PENDING + 1 CONFIRMED
    for st in (AnomalyStatus.PENDING, AnomalyStatus.PENDING, AnomalyStatus.CONFIRMED):
        db_session.add(AnomalyDetection(
            id=generate_cuid(),
            type=AnomalyType.IMPOSSIBLE_GRADE,
            severity=AnomalySeverity.CRITICAL,
            status=st,
            entityType="Grade",
            entityId=generate_cuid(),
            description="test",
            evidence={"x": 1},
            schoolId=school_ctx["school"].id,
            regionId=school_ctx["region"].id,
            detectedAt=now,
        ))
    await db_session.flush()

    service = AnomalyService(db_session)
    items, total = await service.list_anomalies(
        status=AnomalyStatus.PENDING,
        school_id=school_ctx["school"].id,
    )
    assert total == 2
    for item in items:
        assert item.status == AnomalyStatus.PENDING


@pytest.mark.asyncio
async def test_review_anomaly_changes_status(
    db_session: AsyncSession, school_ctx: dict[str, Any], auth_headers: Any,
) -> None:
    factories.bind(db_session)
    headers = await auth_headers(UserRole.SCHOOL_DIRECTOR, schoolId=school_ctx["school"].id)
    del headers  # not used here
    now = datetime.now(UTC)
    anomaly = AnomalyDetection(
        id=generate_cuid(),
        type=AnomalyType.IMPOSSIBLE_GRADE,
        severity=AnomalySeverity.CRITICAL,
        status=AnomalyStatus.PENDING,
        entityType="Grade",
        entityId=generate_cuid(),
        description="test review",
        evidence={"x": 1},
        schoolId=school_ctx["school"].id,
        regionId=school_ctx["region"].id,
        detectedAt=now,
    )
    db_session.add(anomaly)
    await db_session.flush()

    service = AnomalyService(db_session)
    updated = await service.review_anomaly(
        anomaly.id,
        new_status=AnomalyStatus.CONFIRMED,
        note="Vérifié — saisie erronée du prof",
        reviewer_id=None,
    )
    assert updated.status == AnomalyStatus.CONFIRMED
    assert updated.reviewedAt is not None
    assert updated.reviewNote == "Vérifié — saisie erronée du prof"


@pytest.mark.asyncio
async def test_review_anomaly_requires_director_role(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], teacher_headers: dict[str, str],
) -> None:
    now = datetime.now(UTC)
    anomaly = AnomalyDetection(
        id=generate_cuid(),
        type=AnomalyType.IMPOSSIBLE_GRADE,
        severity=AnomalySeverity.CRITICAL,
        status=AnomalyStatus.PENDING,
        entityType="Grade",
        entityId=generate_cuid(),
        description="rbac test",
        evidence={"x": 1},
        schoolId=school_ctx["school"].id,
        regionId=school_ctx["region"].id,
        detectedAt=now,
    )
    db_session.add(anomaly)
    await db_session.flush()

    # TEACHER n'est PAS dans READ_ROLES → 403 sur review
    r = await client.post(
        f"/api/anomalies/{anomaly.id}/review",
        headers=teacher_headers,
        json={"status": "CONFIRMED", "note": "test"},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_stats_returns_counts_per_type(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    now = datetime.now(UTC)
    # 2 IMPOSSIBLE_GRADE + 1 GRADE_JUMP + 1 CONFIRMED + 1 FALSE_POSITIVE
    for (atype, st) in [
        (AnomalyType.IMPOSSIBLE_GRADE, AnomalyStatus.PENDING),
        (AnomalyType.IMPOSSIBLE_GRADE, AnomalyStatus.CONFIRMED),
        (AnomalyType.GRADE_JUMP, AnomalyStatus.PENDING),
        (AnomalyType.SUSPICIOUS_ATTENDANCE, AnomalyStatus.FALSE_POSITIVE),
    ]:
        db_session.add(AnomalyDetection(
            id=generate_cuid(),
            type=atype,
            severity=AnomalySeverity.HIGH,
            status=st,
            entityType="X",
            entityId=generate_cuid(),
            description="stats",
            evidence={},
            schoolId=school_ctx["school"].id,
            regionId=school_ctx["region"].id,
            detectedAt=now,
        ))
    await db_session.flush()

    service = AnomalyService(db_session)
    stats = await service.get_stats(school_id=school_ctx["school"].id)
    assert stats.total == 4
    assert stats.pending == 2
    assert stats.confirmed == 1
    assert stats.falsePositive == 1
    by_type = {bt.type: bt.count for bt in stats.byType}
    assert by_type[AnomalyType.IMPOSSIBLE_GRADE] == 2
    assert by_type[AnomalyType.GRADE_JUMP] == 1
    # confirmationRate = 1 / (1 + 0 + 1) = 0.5
    assert stats.confirmationRate == pytest.approx(0.5)


# ===========================================================================
# 3. Router — RBAC & scope
# ===========================================================================
@pytest.mark.asyncio
async def test_list_respects_territorial_scope(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
) -> None:
    factories.bind(db_session)
    # Anomalie école A (visible par le directeur de l'école A)
    now = datetime.now(UTC)
    db_session.add(AnomalyDetection(
        id=generate_cuid(),
        type=AnomalyType.IMPOSSIBLE_GRADE,
        severity=AnomalySeverity.CRITICAL,
        status=AnomalyStatus.PENDING,
        entityType="Grade",
        entityId=generate_cuid(),
        description="visible",
        evidence={},
        schoolId=school_ctx["school"].id,
        regionId=school_ctx["region"].id,
        detectedAt=now,
    ))
    # Anomalie école B (invisible) — autre region pour bien isoler
    other_tree = await factories.make_territorial_tree()
    db_session.add(AnomalyDetection(
        id=generate_cuid(),
        type=AnomalyType.IMPOSSIBLE_GRADE,
        severity=AnomalySeverity.CRITICAL,
        status=AnomalyStatus.PENDING,
        entityType="Grade",
        entityId=generate_cuid(),
        description="invisible",
        evidence={},
        schoolId=other_tree["school"].id,
        regionId=other_tree["region"].id,
        detectedAt=now,
    ))
    await db_session.flush()

    r = await client.get("/api/anomalies", headers=director_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # Le directeur ne doit voir QUE son école.
    descs = {item["description"] for item in body["items"]}
    assert "visible" in descs
    assert "invisible" not in descs


@pytest.mark.asyncio
async def test_run_endpoint_requires_regional_admin(
    client: AsyncClient, director_headers: dict[str, str],
    regional_headers: dict[str, str],
) -> None:
    # Director (SCHOOL_DIRECTOR) ne peut PAS déclencher un run global
    r = await client.post("/api/anomalies/run", headers=director_headers)
    assert r.status_code == 403, r.text

    # Regional admin peut
    r = await client.post("/api/anomalies/run", headers=regional_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "detected" in body
    assert "ranAt" in body


@pytest.mark.asyncio
async def test_anomaly_evidence_stored_as_jsonb(
    db_session: AsyncSession, school_ctx: dict[str, Any],
) -> None:
    """L'evidence JSONB doit faire un round-trip parfait (clés + types)."""
    factories.bind(db_session)
    now = datetime.now(UTC)
    payload = {
        "studentId": school_ctx["students"][0].id,
        "score": 27.5,
        "thresholds": {"min": 0, "max": 20},
        "fields": ["score", "max"],
    }
    a = AnomalyDetection(
        id=generate_cuid(),
        type=AnomalyType.IMPOSSIBLE_GRADE,
        severity=AnomalySeverity.CRITICAL,
        status=AnomalyStatus.PENDING,
        entityType="Grade",
        entityId=generate_cuid(),
        description="jsonb roundtrip",
        evidence=payload,
        schoolId=school_ctx["school"].id,
        regionId=school_ctx["region"].id,
        detectedAt=now,
    )
    db_session.add(a)
    await db_session.flush()
    # Force un re-fetch sans toucher au state ORM (la session est
    # rollback-only et `expire` provoque un MissingGreenlet sur asyncpg
    # côté SQLAlchemy 2.x).
    stmt = select(AnomalyDetection).where(AnomalyDetection.id == a.id)
    fresh = (await db_session.execute(stmt)).scalar_one()
    assert fresh.evidence["studentId"] == payload["studentId"]
    assert fresh.evidence["score"] == 27.5
    assert fresh.evidence["thresholds"] == {"min": 0, "max": 20}
    assert fresh.evidence["fields"] == ["score", "max"]


# Silence unused-import linter where Gender / date are kept for parity with
# the rest of the test pack.
_ = (Gender, date)
