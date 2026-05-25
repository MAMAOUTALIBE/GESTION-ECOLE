"""Module 5D — Droit à l'oubli (anonymisation post-sortie d'élève).

Couvre :

1.  test_request_erasure_creates_grace_period
2.  test_request_requires_national_or_ministry
3.  test_anonymize_student_replaces_names
4.  test_anonymize_preserves_attendance_records
5.  test_anonymize_preserves_grades_for_aggregates
6.  test_anonymize_deletes_qr_credential
7.  test_anonymize_deletes_orphan_parent
8.  test_anonymize_keeps_parent_with_other_children
9.  test_cancel_during_grace_period_works
10. test_cannot_cancel_after_execution
11. test_execute_pending_only_runs_after_grace_period
12. test_execute_pending_creates_pii_audit_log
13. test_request_unknown_student_returns_404
14. test_list_pending_filters_by_status
15. test_double_request_for_same_student_rejected
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.academics.models import (
    AcademicPeriod,
    Assessment,
    Grade,
    Parent,
    ParentCommunication,
    ReportCard,
    SchoolYear,
    StudentParent,
    Subject,
)
from app.modules.attendance.models import AttendanceRecord, QrCredential
from app.modules.auth.models import User
from app.modules.census.models import Student, StudentTransfer
from app.modules.erasure.anonymizer import anonymize_student
from app.modules.erasure.enums import (
    GRACE_PERIOD_DAYS,
    ErasureReason,
    ErasureStatus,
)
from app.modules.erasure.models import ErasureRequest
from app.modules.erasure.schemas import (
    CancelErasureRequest,
    ErasureRequestCreate,
)
from app.modules.erasure.service import ErasureService
from app.modules.pii_audit.enums import PiiAccessType, PiiEntityType
from app.modules.pii_audit.models import PiiAccessLog
from app.modules.schoollife.enums import AllergyCategory, VaccinationStatus
from app.modules.schoollife.models import (
    HealthVisit,
    Incident,
    StudentAllergy,
    Vaccination,
)
from app.shared.base import generate_cuid
from app.shared.enums import (
    AcademicPeriodType,
    AcademicValidationStatus,
    AssessmentType,
    AttendanceStatus,
    CommunicationChannel,
    CommunicationStatus,
    Gender,
    HealthVisitStatus,
    HealthVisitType,
    IncidentSanction,
    IncidentSeverity,
    IncidentType,
    ParentRelationType,
    PersonType,
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
        email=f"5d-{role.value.lower()}-{uid[:6]}@test.local",
        passwordHash="x",
        fullName=f"Test {role.value}",
        role=role,
        isActive=True,
        **kwargs,
    )
    session.add(user)
    await session.flush()
    return user


async def _make_student(
    db_session: AsyncSession,
    *,
    first_name: str = "Aïssatou",
    last_name: str = "Diallo",
) -> Student:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    school = tree["school"]
    student = await factories.StudentFactory.create_async(
        schoolId=school.id,
        firstName=first_name,
        lastName=last_name,
        gender=Gender.FEMALE,
        photoUrl="https://cdn.test/aissatou.jpg",
        guardianName="Mariam Diallo",
        guardianPhone="+224622123456",
    )
    return student


async def _make_parent(
    db_session: AsyncSession,
    *,
    student_id: str,
    phone: str | None = None,
    relation: ParentRelationType = ParentRelationType.MOTHER,
) -> tuple[Parent, StudentParent]:
    parent = Parent(
        id=generate_cuid(),
        firstName="Mariam",
        lastName="Sow",
        phone=phone or f"+224622{datetime.now(UTC).microsecond:06d}",
    )
    db_session.add(parent)
    await db_session.flush()
    link = StudentParent(
        id=generate_cuid(),
        studentId=student_id,
        parentId=parent.id,
        relation=relation,
        isPrimary=True,
    )
    db_session.add(link)
    await db_session.flush()
    return parent, link


async def _make_school_year(
    db_session: AsyncSession,
    name: str = "2024-2025",
) -> tuple[SchoolYear, AcademicPeriod]:
    year = SchoolYear(
        id=generate_cuid(),
        name=name,
        startDate=datetime(2024, 9, 1, tzinfo=UTC),
        endDate=datetime(2025, 7, 31, tzinfo=UTC),
        periodType=AcademicPeriodType.TRIMESTER,
        isActive=True,
    )
    db_session.add(year)
    await db_session.flush()
    period = AcademicPeriod(
        id=generate_cuid(),
        name="Trimestre 1",
        type=AcademicPeriodType.TRIMESTER,
        order=1,
        schoolYearId=year.id,
    )
    db_session.add(period)
    await db_session.flush()
    return year, period


# ===========================================================================
# 1. request_erasure crée la demande en GRACE_PERIOD avec deadline + 30j
# ===========================================================================
@pytest.mark.asyncio
async def test_request_erasure_creates_grace_period(
    db_session: AsyncSession,
) -> None:
    admin = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    student = await _make_student(db_session)
    svc = ErasureService(db_session)

    before = datetime.now(UTC)
    dto = ErasureRequestCreate(
        studentId=student.id,
        reason=ErasureReason.LEFT_COUNTRY,
        reasonDetails="Famille partie au Sénégal.",
    )
    result = await svc.request_erasure(dto, admin)

    assert result.status == ErasureStatus.GRACE_PERIOD
    assert result.studentId == student.id
    assert result.reason == ErasureReason.LEFT_COUNTRY
    assert result.requestedById == admin.id
    expected_min = before + timedelta(days=GRACE_PERIOD_DAYS - 1)
    expected_max = before + timedelta(days=GRACE_PERIOD_DAYS + 1)
    assert expected_min < result.gracePeriodUntil < expected_max
    # Initiales calculées (élève pas encore anonymisé)
    assert result.studentInitials is not None
    assert result.studentInitials.startswith("A.")


# ===========================================================================
# 2. RBAC : seul NATIONAL/MINISTRY peut demander
# ===========================================================================
@pytest.mark.asyncio
async def test_request_requires_national_or_ministry(
    db_session: AsyncSession,
) -> None:
    director = await _make_user(db_session, UserRole.SCHOOL_DIRECTOR)
    student = await _make_student(db_session)
    svc = ErasureService(db_session)

    dto = ErasureRequestCreate(
        studentId=student.id,
        reason=ErasureReason.EXCLUDED,
    )
    with pytest.raises(ForbiddenError):
        await svc.request_erasure(dto, director)

    # MINISTRY_ADMIN OK
    ministry = await _make_user(db_session, UserRole.MINISTRY_ADMIN)
    result = await svc.request_erasure(dto, ministry)
    assert result.status == ErasureStatus.GRACE_PERIOD


# ===========================================================================
# 3. anonymize_student remplace bien les noms et NULLifie les champs
# ===========================================================================
@pytest.mark.asyncio
async def test_anonymize_student_replaces_names(
    db_session: AsyncSession,
) -> None:
    student = await _make_student(
        db_session,
        first_name="Mamadou",
        last_name="Diallo",
    )
    assert student.photoUrl is not None
    assert student.guardianName is not None
    assert student.guardianPhone is not None

    counts = await anonymize_student(db_session, student.id)
    assert counts["Student"] == 1

    refreshed = (
        await db_session.execute(
            select(Student).where(Student.id == student.id)
        )
    ).scalars().one()
    assert refreshed.firstName == "Anonyme"
    assert refreshed.lastName == "Anonyme"
    assert refreshed.photoUrl is None
    assert refreshed.guardianName is None
    assert refreshed.guardianPhone is None
    # uniqueCode préservé pour audits a posteriori
    assert refreshed.uniqueCode == student.uniqueCode


# ===========================================================================
# 4. AttendanceRecord préservé (agrégats Module 1A)
# ===========================================================================
@pytest.mark.asyncio
async def test_anonymize_preserves_attendance_records(
    db_session: AsyncSession,
) -> None:
    student = await _make_student(db_session)
    school_id = student.schoolId

    # Insère 5 AttendanceRecord
    for i in range(5):
        record = AttendanceRecord(
            id=generate_cuid(),
            personType=PersonType.STUDENT,
            status=AttendanceStatus.PRESENT,
            scannedAt=datetime(2025, 1, 10 + i, 8, 0, tzinfo=UTC),
            schoolId=school_id,
            studentId=student.id,
        )
        db_session.add(record)
    await db_session.flush()

    counts = await anonymize_student(db_session, student.id)
    assert counts["AttendanceRecord"] == 5

    remaining = (
        await db_session.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.studentId == student.id
            )
        )
    ).scalars().all()
    # AttendanceRecord PRESERVES — toutes les rows restent, studentId aussi
    assert len(list(remaining)) == 5


# ===========================================================================
# 5. Grade + ReportCard préservés pour agrégats
# ===========================================================================
@pytest.mark.asyncio
async def test_anonymize_preserves_grades_for_aggregates(
    db_session: AsyncSession,
) -> None:
    student = await _make_student(db_session)
    year, period = await _make_school_year(db_session)
    factories.bind(db_session)
    classroom = await factories.ClassRoomFactory.create_async(
        schoolId=student.schoolId,
    )
    subject = Subject(
        id=generate_cuid(),
        code=f"MATH-{generate_cuid()[:6]}",
        name="Mathématiques",
        coefficient=2.0,
    )
    db_session.add(subject)
    await db_session.flush()
    assessment = Assessment(
        id=generate_cuid(),
        title="Compo Trim. 1",
        type=AssessmentType.COMPOSITION,
        coefficient=2.0,
        maxScore=20.0,
        schoolYearId=year.id,
        periodId=period.id,
        subjectId=subject.id,
        classRoomId=classroom.id,
        status=AcademicValidationStatus.VALIDATED,
    )
    db_session.add(assessment)
    await db_session.flush()

    grade = Grade(
        id=generate_cuid(),
        assessmentId=assessment.id,
        studentId=student.id,
        schoolYearId=year.id,
        periodId=period.id,
        subjectId=subject.id,
        classRoomId=classroom.id,
        score=15.5,
        status=AcademicValidationStatus.VALIDATED,
        recordedAt=datetime.now(UTC),
        updatedAt=datetime.now(UTC),
    )
    db_session.add(grade)
    rc = ReportCard(
        id=generate_cuid(),
        studentId=student.id,
        classRoomId=classroom.id,
        schoolYearId=year.id,
        periodId=period.id,
        average=15.5,
        verificationCode=f"VER-{generate_cuid()[:10]}",
        status=AcademicValidationStatus.VALIDATED,
    )
    db_session.add(rc)
    await db_session.flush()

    counts = await anonymize_student(db_session, student.id)
    assert counts["Grade"] == 1
    assert counts["ReportCard"] == 1

    grade_check = (
        await db_session.execute(
            select(Grade).where(Grade.studentId == student.id)
        )
    ).scalars().one()
    assert grade_check.score == 15.5  # agrégat préservé
    rc_check = (
        await db_session.execute(
            select(ReportCard).where(ReportCard.studentId == student.id)
        )
    ).scalars().one()
    assert rc_check.average == 15.5


# ===========================================================================
# 6. QrCredential supprimé (payload nominatif)
# ===========================================================================
@pytest.mark.asyncio
async def test_anonymize_deletes_qr_credential(
    db_session: AsyncSession,
) -> None:
    student = await _make_student(db_session)
    qr = QrCredential(
        id=generate_cuid(),
        token=f"tok-{generate_cuid()[:10]}",
        payload='{"firstName":"Aïssatou","lastName":"Diallo"}',
        personType=PersonType.STUDENT,
        studentId=student.id,
    )
    db_session.add(qr)
    await db_session.flush()

    counts = await anonymize_student(db_session, student.id)
    assert counts["QrCredential"] == 1

    remaining = (
        await db_session.execute(
            select(QrCredential).where(QrCredential.studentId == student.id)
        )
    ).scalars().all()
    assert len(list(remaining)) == 0


# ===========================================================================
# 7. Parent orphelin supprimé (un seul enfant lié, anonymisé)
# ===========================================================================
@pytest.mark.asyncio
async def test_anonymize_deletes_orphan_parent(
    db_session: AsyncSession,
) -> None:
    student = await _make_student(db_session)
    parent, _ = await _make_parent(
        db_session, student_id=student.id, phone="+224622900001"
    )

    counts = await anonymize_student(db_session, student.id)
    assert counts["StudentParent"] == 1
    assert counts["Parent"] == 1  # parent orphelin → delete

    remaining_parent = (
        await db_session.execute(
            select(Parent).where(Parent.id == parent.id)
        )
    ).scalars().one_or_none()
    assert remaining_parent is None


# ===========================================================================
# 8. Parent conservé si lié à un autre enfant
# ===========================================================================
@pytest.mark.asyncio
async def test_anonymize_keeps_parent_with_other_children(
    db_session: AsyncSession,
) -> None:
    student_a = await _make_student(
        db_session, first_name="Alice", last_name="Sow"
    )
    factories.bind(db_session)
    student_b = await factories.StudentFactory.create_async(
        schoolId=student_a.schoolId,
        firstName="Boris",
        lastName="Sow",
        gender=Gender.MALE,
    )
    parent, _ = await _make_parent(
        db_session, student_id=student_a.id, phone="+224622900002"
    )
    # Lien parent <-> student_b
    db_session.add(
        StudentParent(
            id=generate_cuid(),
            studentId=student_b.id,
            parentId=parent.id,
            relation=ParentRelationType.MOTHER,
            isPrimary=True,
        )
    )
    await db_session.flush()

    counts = await anonymize_student(db_session, student_a.id)
    assert counts["StudentParent"] == 1
    assert counts["Parent"] == 0  # parent conservé (lien vers student_b)

    still_there = (
        await db_session.execute(
            select(Parent).where(Parent.id == parent.id)
        )
    ).scalars().one_or_none()
    assert still_there is not None


# ===========================================================================
# 9. cancel_erasure pendant la grace period
# ===========================================================================
@pytest.mark.asyncio
async def test_cancel_during_grace_period_works(
    db_session: AsyncSession,
) -> None:
    admin = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    student = await _make_student(db_session)
    svc = ErasureService(db_session)

    created = await svc.request_erasure(
        ErasureRequestCreate(
            studentId=student.id,
            reason=ErasureReason.OTHER,
            reasonDetails="Test cancel",
        ),
        admin,
    )
    assert created.status == ErasureStatus.GRACE_PERIOD

    cancelled = await svc.cancel_erasure(
        created.id,
        CancelErasureRequest(cancellationReason="Erreur d'identification"),
        admin,
    )
    assert cancelled.status == ErasureStatus.CANCELLED
    assert cancelled.cancellationReason == "Erreur d'identification"
    assert cancelled.cancelledById == admin.id

    # Le student n'a PAS été anonymisé
    refreshed = (
        await db_session.execute(
            select(Student).where(Student.id == student.id)
        )
    ).scalars().one()
    assert refreshed.firstName != "Anonyme"


# ===========================================================================
# 10. cancel impossible après EXECUTED
# ===========================================================================
@pytest.mark.asyncio
async def test_cannot_cancel_after_execution(
    db_session: AsyncSession,
) -> None:
    admin = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    student = await _make_student(db_session)
    svc = ErasureService(db_session)

    created = await svc.request_erasure(
        ErasureRequestCreate(
            studentId=student.id,
            reason=ErasureReason.EXCLUDED,
        ),
        admin,
    )

    # Force gracePeriodUntil dans le passé pour le batch
    row = (
        await db_session.execute(
            select(ErasureRequest).where(ErasureRequest.id == created.id)
        )
    ).scalars().one()
    row.gracePeriodUntil = datetime.now(UTC) - timedelta(days=1)
    await db_session.flush()

    await svc.execute_pending_erasures(admin)

    # Tentative d'annulation → ConflictError
    with pytest.raises(ConflictError):
        await svc.cancel_erasure(
            created.id,
            CancelErasureRequest(cancellationReason="Trop tard"),
            admin,
        )


# ===========================================================================
# 11. execute_pending n'exécute QUE les demandes dont gracePeriodUntil < now
# ===========================================================================
@pytest.mark.asyncio
async def test_execute_pending_only_runs_after_grace_period(
    db_session: AsyncSession,
) -> None:
    admin = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    student_old = await _make_student(
        db_session, first_name="Vieux", last_name="Eleve"
    )
    student_new = await _make_student(
        db_session, first_name="Nouveau", last_name="Eleve"
    )
    svc = ErasureService(db_session)

    old_req = await svc.request_erasure(
        ErasureRequestCreate(
            studentId=student_old.id,
            reason=ErasureReason.LEFT_COUNTRY,
        ),
        admin,
    )
    new_req = await svc.request_erasure(
        ErasureRequestCreate(
            studentId=student_new.id,
            reason=ErasureReason.LEFT_COUNTRY,
        ),
        admin,
    )

    # Force la première dans le passé (grace écoulée), laisse l'autre dans le futur
    row_old = (
        await db_session.execute(
            select(ErasureRequest).where(ErasureRequest.id == old_req.id)
        )
    ).scalars().one()
    row_old.gracePeriodUntil = datetime.now(UTC) - timedelta(days=1)
    await db_session.flush()

    result = await svc.execute_pending_erasures(admin)
    assert result["executed"] == 1
    assert result["skipped"] == 0

    # student_old anonymisé, student_new intact
    s_old = (
        await db_session.execute(
            select(Student).where(Student.id == student_old.id)
        )
    ).scalars().one()
    assert s_old.firstName == "Anonyme"
    s_new = (
        await db_session.execute(
            select(Student).where(Student.id == student_new.id)
        )
    ).scalars().one()
    assert s_new.firstName == "Nouveau"

    # Vérifie le statut
    row_old_after = (
        await db_session.execute(
            select(ErasureRequest).where(ErasureRequest.id == old_req.id)
        )
    ).scalars().one()
    assert row_old_after.status == ErasureStatus.EXECUTED
    assert row_old_after.executedAt is not None
    assert row_old_after.executedById == admin.id

    row_new_after = (
        await db_session.execute(
            select(ErasureRequest).where(ErasureRequest.id == new_req.id)
        )
    ).scalars().one()
    assert row_new_after.status == ErasureStatus.GRACE_PERIOD


# ===========================================================================
# 12. execute_pending crée bien un PiiAccessLog (EXECUTE_ERASURE)
# ===========================================================================
@pytest.mark.asyncio
async def test_execute_pending_creates_pii_audit_log(
    db_session: AsyncSession,
) -> None:
    admin = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    student = await _make_student(db_session)
    svc = ErasureService(db_session)

    created = await svc.request_erasure(
        ErasureRequestCreate(
            studentId=student.id,
            reason=ErasureReason.LEFT_COUNTRY,
        ),
        admin,
    )
    # Force grace écoulée
    row = (
        await db_session.execute(
            select(ErasureRequest).where(ErasureRequest.id == created.id)
        )
    ).scalars().one()
    row.gracePeriodUntil = datetime.now(UTC) - timedelta(hours=1)
    await db_session.flush()

    await svc.execute_pending_erasures(admin)

    logs = (
        await db_session.execute(
            select(PiiAccessLog).where(
                PiiAccessLog.entityType == PiiEntityType.STUDENT,
                PiiAccessLog.entityId == student.id,
                PiiAccessLog.accessType == PiiAccessType.EXPORT,
            )
        )
    ).scalars().all()
    # On a au minimum 2 lignes : REQUEST_ERASURE + EXECUTE_ERASURE
    assert len(logs) >= 2
    actions = {
        log.metadataJson.get("action")
        for log in logs
        if log.metadataJson is not None
    }
    assert "REQUEST_ERASURE" in actions
    assert "EXECUTE_ERASURE" in actions


# ===========================================================================
# 13. request avec studentId inconnu → 404
# ===========================================================================
@pytest.mark.asyncio
async def test_request_unknown_student_returns_404(
    db_session: AsyncSession,
) -> None:
    admin = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    svc = ErasureService(db_session)

    with pytest.raises(NotFoundError):
        await svc.request_erasure(
            ErasureRequestCreate(
                studentId="unknown-student-id",
                reason=ErasureReason.OTHER,
                reasonDetails="inexistant",
            ),
            admin,
        )


# ===========================================================================
# 14. list_pending_erasures filtre bien par statut
# ===========================================================================
@pytest.mark.asyncio
async def test_list_pending_filters_by_status(
    db_session: AsyncSession,
) -> None:
    admin = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    s1 = await _make_student(db_session, first_name="A", last_name="Un")
    s2 = await _make_student(db_session, first_name="B", last_name="Deux")
    svc = ErasureService(db_session)

    # 2 demandes, on annule la seconde
    r1 = await svc.request_erasure(
        ErasureRequestCreate(studentId=s1.id, reason=ErasureReason.OTHER),
        admin,
    )
    r2 = await svc.request_erasure(
        ErasureRequestCreate(studentId=s2.id, reason=ErasureReason.OTHER),
        admin,
    )
    await svc.cancel_erasure(
        r2.id,
        CancelErasureRequest(cancellationReason="Annulée"),
        admin,
    )

    pending = await svc.list_pending_erasures(
        admin, status=ErasureStatus.GRACE_PERIOD,
    )
    pending_ids = {r.id for r in pending}
    assert r1.id in pending_ids
    assert r2.id not in pending_ids

    cancelled = await svc.list_pending_erasures(
        admin, status=ErasureStatus.CANCELLED,
    )
    cancelled_ids = {r.id for r in cancelled}
    assert r2.id in cancelled_ids
    assert r1.id not in cancelled_ids


# ===========================================================================
# 15. Double demande pour le MÊME élève → ConflictError
# ===========================================================================
@pytest.mark.asyncio
async def test_double_request_for_same_student_rejected(
    db_session: AsyncSession,
) -> None:
    admin = await _make_user(db_session, UserRole.NATIONAL_ADMIN)
    student = await _make_student(db_session)
    svc = ErasureService(db_session)

    await svc.request_erasure(
        ErasureRequestCreate(
            studentId=student.id,
            reason=ErasureReason.LEFT_COUNTRY,
        ),
        admin,
    )

    with pytest.raises(ConflictError):
        await svc.request_erasure(
            ErasureRequestCreate(
                studentId=student.id,
                reason=ErasureReason.OTHER,
            ),
            admin,
        )


# ===========================================================================
# 16 (bonus) — Incident / HealthVisit / Vaccination / Allergy / Transfer
# ===========================================================================
@pytest.mark.asyncio
async def test_anonymize_redacts_or_deletes_secondary_pii(
    db_session: AsyncSession,
) -> None:
    """Vérifie les autres tables sensibles touchées par l'anonymisation."""
    student = await _make_student(
        db_session, first_name="Sékou", last_name="Touré"
    )
    school_id = student.schoolId

    # Incident
    inc = Incident(
        id=generate_cuid(),
        schoolId=school_id,
        studentId=student.id,
        type=IncidentType.FIGHTING,
        severity=IncidentSeverity.MEDIUM,
        description="Sékou a frappé un camarade dans la cour.",
        sanction=IncidentSanction.WARNING,
        occurredAt=datetime.now(UTC),
    )
    # HealthVisit
    hv = HealthVisit(
        id=generate_cuid(),
        schoolId=school_id,
        studentId=student.id,
        type=HealthVisitType.ILLNESS,
        description="Sékou a eu une crise d'asthme. Infirmière Aïssatou.",
        visitDate=date(2025, 3, 5),
        nurseName="Aïssatou Bah",
        status=HealthVisitStatus.TREATED,
    )
    # Vaccination
    vacc = Vaccination(
        id=generate_cuid(),
        studentId=student.id,
        vaccine="BCG",
        dateAdministered=date(2025, 1, 12),
        notes="Réaction normale pour Sékou.",
        status=VaccinationStatus.ADMINISTERED,
    )
    # Allergy
    allergy = StudentAllergy(
        id=generate_cuid(),
        studentId=student.id,
        allergen="Arachide",
        category=AllergyCategory.FOOD,
        notes="Allergie sévère pour Sékou.",
    )
    # ParentCommunication. Le parent est aussi lié à un AUTRE enfant
    # pour qu'il survive à l'anonymisation (sinon il serait supprimé
    # comme orphelin avec ses ParentCommunication en cascade).
    factories.bind(db_session)
    sibling = await factories.StudentFactory.create_async(
        schoolId=school_id,
        firstName="Aminata",
        lastName="Toure",
        gender=Gender.FEMALE,
    )
    parent, _ = await _make_parent(
        db_session, student_id=student.id, phone="+224622900003"
    )
    db_session.add(
        StudentParent(
            id=generate_cuid(),
            studentId=sibling.id,
            parentId=parent.id,
            relation=ParentRelationType.MOTHER,
            isPrimary=False,
        )
    )
    await db_session.flush()
    pc = ParentCommunication(
        id=generate_cuid(),
        parentId=parent.id,
        studentId=student.id,
        channel=CommunicationChannel.SMS,
        status=CommunicationStatus.SENT,
        subject="Bulletin de Sekou",
        message="Bonjour, le bulletin de Sekou est disponible.",
    )
    # StudentTransfer (besoin d'une école destination)
    factories.bind(db_session)
    other_school = await factories.SchoolFactory.create_async(
        regionId=(await factories.RegionFactory.create_async()).id,
    )
    transfer = StudentTransfer(
        id=generate_cuid(),
        studentId=student.id,
        fromSchoolId=school_id,
        toSchoolId=other_school.id,
        reason="Sékou déménage à Conakry.",
        transferredAt=datetime.now(UTC),
    )
    db_session.add_all([inc, hv, vacc, allergy, pc, transfer])
    await db_session.flush()

    counts = await anonymize_student(db_session, student.id)
    assert counts["Incident"] == 1
    assert counts["HealthVisit"] == 1
    assert counts["Vaccination"] == 1
    assert counts["StudentAllergy"] == 1
    assert counts["ParentCommunication"] == 1
    assert counts["StudentTransfer"] == 1

    # Incident : description redactée, studentId NULL
    inc_after = (
        await db_session.execute(
            select(Incident).where(Incident.id == inc.id)
        )
    ).scalars().one()
    assert inc_after.description == "[ANONYMISÉ]"
    assert inc_after.studentId is None

    # HealthVisit : description + nurseName NULL, studentId NULL
    hv_after = (
        await db_session.execute(
            select(HealthVisit).where(HealthVisit.id == hv.id)
        )
    ).scalars().one()
    assert hv_after.description == "[ANONYMISÉ]"
    assert hv_after.nurseName is None
    assert hv_after.studentId is None

    # Vaccination supprimée (FK NOT NULL)
    vacc_after = (
        await db_session.execute(
            select(Vaccination).where(Vaccination.id == vacc.id)
        )
    ).scalars().one_or_none()
    assert vacc_after is None

    # Allergy supprimée (FK NOT NULL)
    allergy_after = (
        await db_session.execute(
            select(StudentAllergy).where(StudentAllergy.id == allergy.id)
        )
    ).scalars().one_or_none()
    assert allergy_after is None

    # ParentCommunication : subject + message redactés
    pc_after = (
        await db_session.execute(
            select(ParentCommunication).where(ParentCommunication.id == pc.id)
        )
    ).scalars().one()
    assert pc_after.subject == "[ANONYMISÉ]"
    assert pc_after.message == "[ANONYMISÉ]"

    # Transfer : reason redacté, row conservé
    tr_after = (
        await db_session.execute(
            select(StudentTransfer).where(StudentTransfer.id == transfer.id)
        )
    ).scalars().one()
    assert tr_after.reason == "[ANONYMISÉ]"
