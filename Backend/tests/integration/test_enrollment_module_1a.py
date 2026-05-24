"""Module 1A — Enrollment désagrégé (niveau × genre).

Couvre :
1. POST unitaire — validation des données.
2. POST unitaire — rejet count < 0.
3. POST unitaire — rejet doublon (year, school, level, gender, source).
4. POST bulk — insertion multiple.
5. POST bulk — erreurs par item ne cassent pas le batch.
6. POST bulk — max 200 items.
7. GET list — ne retourne que l'école demandée.
8. GET aggregate national — somme toutes les écoles dans le scope.
9. GET aggregate régional — filtre par région.
10. GET aggregate breakdown niveau × genre.
11. compute_from_students — agrège la base Student.
12. compute_from_students — réservé NATIONAL_ADMIN/MINISTRY_ADMIN.
13. POST — TEACHER refusé, CENSUS_AGENT accepté.
14. GET — SCHOOL_DIRECTOR ne voit que son école.
15. GET aggregate — REGIONAL_ADMIN respecte son scope.
16. Unique constraint — IntegrityError direct sur l'ORM.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import SchoolYear
from app.modules.enrollment.enums import EnrollmentClassLevel, EnrollmentSource
from app.modules.enrollment.models import Enrollment
from app.modules.enrollment.service import EnrollmentService
from app.shared.base import generate_cuid
from app.shared.enums import AcademicPeriodType, Gender, UserRole
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
async def _make_school_year(session: AsyncSession) -> SchoolYear:
    year = SchoolYear(
        id=generate_cuid(),
        name=f"YEAR-{generate_cuid()[:6]}",
        startDate=datetime(2025, 9, 1, tzinfo=UTC),
        endDate=datetime(2026, 6, 30, tzinfo=UTC),
        periodType=AcademicPeriodType.TRIMESTER,
        isActive=True,
    )
    session.add(year)
    await session.flush()
    return year


@pytest_asyncio.fixture(loop_scope="session")
async def enroll_ctx(db_session: AsyncSession) -> dict[str, Any]:
    """Un mini-tree territorial + 1 école + 1 SchoolYear."""
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    year = await _make_school_year(db_session)
    return {
        "region": tree["region"],
        "prefecture": tree["prefecture"],
        "subPrefecture": tree["subPrefecture"],
        "school": tree["school"],
        "year": year,
    }


# ===========================================================================
# 1. POST unitaire — création valide
# ===========================================================================
async def test_record_creates_enrollment_with_valid_data(
    client: AsyncClient,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    headers = await auth_headers(
        UserRole.NATIONAL_ADMIN, email="nat-create@test.local",
    )
    payload = {
        "schoolYearId": enroll_ctx["year"].id,
        "schoolId": enroll_ctx["school"].id,
        "classLevel": EnrollmentClassLevel.CP1.value,
        "gender": Gender.FEMALE.value,
        "count": 42,
        "source": EnrollmentSource.CENSUS_DECLARED.value,
    }
    r = await client.post("/api/enrollment", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["count"] == 42
    assert body["classLevel"] == "CP1"
    assert body["gender"] == "FEMALE"
    assert body["source"] == "CENSUS_DECLARED"
    assert body["recordedById"] is not None


# ===========================================================================
# 2. POST unitaire — count < 0 rejeté par Pydantic
# ===========================================================================
async def test_record_rejects_negative_count(
    client: AsyncClient,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    headers = await auth_headers(
        UserRole.NATIONAL_ADMIN, email="nat-neg@test.local",
    )
    payload = {
        "schoolYearId": enroll_ctx["year"].id,
        "schoolId": enroll_ctx["school"].id,
        "classLevel": EnrollmentClassLevel.CP1.value,
        "gender": Gender.FEMALE.value,
        "count": -5,
        "source": EnrollmentSource.CENSUS_DECLARED.value,
    }
    r = await client.post("/api/enrollment", json=payload, headers=headers)
    assert r.status_code == 422, r.text


# ===========================================================================
# 3. POST unitaire — doublon
# ===========================================================================
async def test_record_rejects_duplicate_same_year_school_level_gender_source(
    client: AsyncClient,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    headers = await auth_headers(
        UserRole.NATIONAL_ADMIN, email="nat-dup@test.local",
    )
    payload = {
        "schoolYearId": enroll_ctx["year"].id,
        "schoolId": enroll_ctx["school"].id,
        "classLevel": EnrollmentClassLevel.CE1.value,
        "gender": Gender.MALE.value,
        "count": 10,
        "source": EnrollmentSource.CENSUS_DECLARED.value,
    }
    r1 = await client.post("/api/enrollment", json=payload, headers=headers)
    assert r1.status_code == 201, r1.text
    r2 = await client.post("/api/enrollment", json=payload, headers=headers)
    assert r2.status_code == 409, r2.text


# ===========================================================================
# 4. POST bulk — insertion multiple
# ===========================================================================
async def test_bulk_record_inserts_multiple(
    client: AsyncClient,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    headers = await auth_headers(
        UserRole.NATIONAL_ADMIN, email="nat-bulk@test.local",
    )
    items = [
        {
            "schoolYearId": enroll_ctx["year"].id,
            "schoolId": enroll_ctx["school"].id,
            "classLevel": level.value,
            "gender": gender.value,
            "count": 12,
            "source": EnrollmentSource.CENSUS_DECLARED.value,
        }
        for level in (
            EnrollmentClassLevel.CP1,
            EnrollmentClassLevel.CP2,
            EnrollmentClassLevel.CE1,
        )
        for gender in (Gender.FEMALE, Gender.MALE)
    ]
    r = await client.post(
        "/api/enrollment/bulk",
        json={"items": items},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 6
    assert body["errors"] == []


# ===========================================================================
# 5. POST bulk — erreurs par item ne cassent pas le batch
# ===========================================================================
async def test_bulk_record_returns_errors_for_invalid_items(
    client: AsyncClient,
    db_session: AsyncSession,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    """Pré-insère une row puis envoie 3 items dont 1 duplique : on doit
    avoir 2 inserted + 1 erreur."""
    headers = await auth_headers(
        UserRole.NATIONAL_ADMIN, email="nat-bulkerr@test.local",
    )
    # Pré-insertion d'une row qui rendra l'item #2 du bulk en conflit.
    pre_existing = Enrollment(
        schoolYearId=enroll_ctx["year"].id,
        schoolId=enroll_ctx["school"].id,
        classLevel=EnrollmentClassLevel.CM1,
        gender=Gender.FEMALE,
        count=20,
        source=EnrollmentSource.CENSUS_DECLARED,
        recordedAt=datetime.now(UTC),
    )
    db_session.add(pre_existing)
    await db_session.flush()

    items = [
        # item 0 — OK (niveau différent)
        {
            "schoolYearId": enroll_ctx["year"].id,
            "schoolId": enroll_ctx["school"].id,
            "classLevel": EnrollmentClassLevel.CM2.value,
            "gender": Gender.FEMALE.value,
            "count": 18,
            "source": EnrollmentSource.CENSUS_DECLARED.value,
        },
        # item 1 — CONFLIT (même clé que pre_existing)
        {
            "schoolYearId": enroll_ctx["year"].id,
            "schoolId": enroll_ctx["school"].id,
            "classLevel": EnrollmentClassLevel.CM1.value,
            "gender": Gender.FEMALE.value,
            "count": 99,
            "source": EnrollmentSource.CENSUS_DECLARED.value,
        },
        # item 2 — OK
        {
            "schoolYearId": enroll_ctx["year"].id,
            "schoolId": enroll_ctx["school"].id,
            "classLevel": EnrollmentClassLevel.CM2.value,
            "gender": Gender.MALE.value,
            "count": 22,
            "source": EnrollmentSource.CENSUS_DECLARED.value,
        },
    ]
    r = await client.post(
        "/api/enrollment/bulk", json={"items": items}, headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 2
    assert len(body["errors"]) == 1
    assert body["errors"][0]["index"] == 1


# ===========================================================================
# 6. POST bulk — max 200 items
# ===========================================================================
async def test_bulk_record_respects_max_200_items(
    client: AsyncClient,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    headers = await auth_headers(
        UserRole.NATIONAL_ADMIN, email="nat-bulkmax@test.local",
    )
    # 201 items → Pydantic rejette dès la validation du payload (422).
    items = [
        {
            "schoolYearId": enroll_ctx["year"].id,
            "schoolId": enroll_ctx["school"].id,
            "classLevel": EnrollmentClassLevel.CP1.value,
            "gender": Gender.FEMALE.value,
            "count": i,
            "source": EnrollmentSource.CENSUS_DECLARED.value,
        }
        for i in range(201)
    ]
    r = await client.post(
        "/api/enrollment/bulk", json={"items": items}, headers=headers
    )
    assert r.status_code == 422, r.text


# ===========================================================================
# 7. GET list — restreint à une seule école
# ===========================================================================
async def test_list_for_school_returns_only_that_school(
    client: AsyncClient,
    db_session: AsyncSession,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    factories.bind(db_session)
    other_school = await factories.SchoolFactory.create_async(
        regionId=enroll_ctx["region"].id,
    )
    # Une row pour chaque école.
    for sch in (enroll_ctx["school"], other_school):
        db_session.add(
            Enrollment(
                schoolYearId=enroll_ctx["year"].id,
                schoolId=sch.id,
                classLevel=EnrollmentClassLevel.CP1,
                gender=Gender.FEMALE,
                count=15,
                source=EnrollmentSource.CENSUS_DECLARED,
                recordedAt=datetime.now(UTC),
            )
        )
    await db_session.flush()

    headers = await auth_headers(
        UserRole.NATIONAL_ADMIN, email="nat-list@test.local",
    )
    r = await client.get(
        f"/api/enrollment/school/{enroll_ctx['school'].id}",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["schoolId"] == enroll_ctx["school"].id


# ===========================================================================
# 8. GET aggregate national — somme toutes les écoles
# ===========================================================================
async def test_aggregate_national_sums_all_schools(
    client: AsyncClient,
    db_session: AsyncSession,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    factories.bind(db_session)
    other_school = await factories.SchoolFactory.create_async(
        regionId=enroll_ctx["region"].id,
    )
    for sch, count in (
        (enroll_ctx["school"], 30),
        (other_school, 25),
    ):
        db_session.add(
            Enrollment(
                schoolYearId=enroll_ctx["year"].id,
                schoolId=sch.id,
                classLevel=EnrollmentClassLevel.CP1,
                gender=Gender.FEMALE,
                count=count,
                source=EnrollmentSource.CENSUS_DECLARED,
                recordedAt=datetime.now(UTC),
            )
        )
    await db_session.flush()

    headers = await auth_headers(
        UserRole.NATIONAL_ADMIN, email="nat-agg@test.local",
    )
    r = await client.get(
        "/api/enrollment/aggregate",
        params={
            "scope": "NATIONAL",
            "schoolYearId": enroll_ctx["year"].id,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 55
    assert body["scope"] == "NATIONAL"


# ===========================================================================
# 9. GET aggregate régional — filtre par regionId
# ===========================================================================
async def test_aggregate_regional_filters_by_region(
    client: AsyncClient,
    db_session: AsyncSession,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    factories.bind(db_session)
    # Une école dans une autre région.
    other_region = await factories.RegionFactory.create_async()
    school_other_region = await factories.SchoolFactory.create_async(
        regionId=other_region.id,
    )
    db_session.add_all([
        Enrollment(
            schoolYearId=enroll_ctx["year"].id,
            schoolId=enroll_ctx["school"].id,
            classLevel=EnrollmentClassLevel.CP1,
            gender=Gender.FEMALE,
            count=10,
            source=EnrollmentSource.CENSUS_DECLARED,
            recordedAt=datetime.now(UTC),
        ),
        Enrollment(
            schoolYearId=enroll_ctx["year"].id,
            schoolId=school_other_region.id,
            classLevel=EnrollmentClassLevel.CP1,
            gender=Gender.FEMALE,
            count=99,
            source=EnrollmentSource.CENSUS_DECLARED,
            recordedAt=datetime.now(UTC),
        ),
    ])
    await db_session.flush()

    headers = await auth_headers(
        UserRole.NATIONAL_ADMIN, email="nat-aggreg@test.local",
    )
    r = await client.get(
        "/api/enrollment/aggregate",
        params={
            "scope": "REGIONAL",
            "schoolYearId": enroll_ctx["year"].id,
            "regionId": enroll_ctx["region"].id,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 10  # seule la région demandée


# ===========================================================================
# 10. GET aggregate breakdown niveau × genre
# ===========================================================================
async def test_aggregate_breakdown_by_gender_and_level(
    client: AsyncClient,
    db_session: AsyncSession,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    rows_to_add = [
        (EnrollmentClassLevel.CP1, Gender.FEMALE, 20),
        (EnrollmentClassLevel.CP1, Gender.MALE, 25),
        (EnrollmentClassLevel.CE1, Gender.FEMALE, 18),
        (EnrollmentClassLevel.CE1, Gender.MALE, 22),
    ]
    for level, gender, count in rows_to_add:
        db_session.add(
            Enrollment(
                schoolYearId=enroll_ctx["year"].id,
                schoolId=enroll_ctx["school"].id,
                classLevel=level,
                gender=gender,
                count=count,
                source=EnrollmentSource.CENSUS_DECLARED,
                recordedAt=datetime.now(UTC),
            )
        )
    await db_session.flush()

    headers = await auth_headers(
        UserRole.NATIONAL_ADMIN, email="nat-brk@test.local",
    )
    r = await client.get(
        "/api/enrollment/aggregate",
        params={
            "scope": "NATIONAL",
            "schoolYearId": enroll_ctx["year"].id,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 85

    by_level_map = {row["level"]: row for row in body["byLevel"]}
    assert by_level_map["CP1"]["count"] == 45
    # GPI CP1 = filles/garçons = 20/25 = 0.8
    assert by_level_map["CP1"]["gpi"] == pytest.approx(0.8, rel=1e-2)

    by_gender_map = {row["gender"]: row for row in body["byGender"]}
    assert by_gender_map["FEMALE"]["count"] == 38
    assert by_gender_map["MALE"]["count"] == 47

    # Breakdown : 4 cellules
    assert len(body["breakdown"]) == 4


# ===========================================================================
# 11. compute_from_students — agrège les Student → Enrollment
# ===========================================================================
async def test_compute_from_students_creates_aggregated_enrollments(
    client: AsyncClient,
    db_session: AsyncSession,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    factories.bind(db_session)
    classroom = await factories.ClassRoomFactory.create_async(
        schoolId=enroll_ctx["school"].id, level="CP1",
    )
    # 3 filles + 2 garçons.
    for _ in range(3):
        await factories.StudentFactory.create_async(
            schoolId=enroll_ctx["school"].id,
            classRoomId=classroom.id,
            gender=Gender.FEMALE,
        )
    for _ in range(2):
        await factories.StudentFactory.create_async(
            schoolId=enroll_ctx["school"].id,
            classRoomId=classroom.id,
            gender=Gender.MALE,
        )

    headers = await auth_headers(
        UserRole.NATIONAL_ADMIN, email="nat-compute@test.local",
    )
    r = await client.post(
        "/api/enrollment/compute-from-students",
        params={"schoolYearId": enroll_ctx["year"].id},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 2  # une row par (gender) pour ce niveau

    # Vérif côté DB
    rows = (
        await db_session.execute(
            select(Enrollment).where(
                Enrollment.source == EnrollmentSource.COMPUTED_FROM_STUDENTS
            )
        )
    ).scalars().all()
    by_gender = {r.gender: r.count for r in rows}
    assert by_gender[Gender.FEMALE] == 3
    assert by_gender[Gender.MALE] == 2


# ===========================================================================
# 12. compute_from_students — réservé NATIONAL/MINISTRY admin
# ===========================================================================
async def test_compute_from_students_requires_national_admin(
    client: AsyncClient,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    headers = await auth_headers(
        UserRole.REGIONAL_ADMIN,
        email="reg-compute@test.local",
        regionId=enroll_ctx["region"].id,
    )
    r = await client.post(
        "/api/enrollment/compute-from-students",
        params={"schoolYearId": enroll_ctx["year"].id},
        headers=headers,
    )
    assert r.status_code == 403, r.text


# ===========================================================================
# 13. RBAC — TEACHER refusé, CENSUS_AGENT accepté
# ===========================================================================
async def test_record_requires_census_write_role(
    client: AsyncClient,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    payload = {
        "schoolYearId": enroll_ctx["year"].id,
        "schoolId": enroll_ctx["school"].id,
        "classLevel": EnrollmentClassLevel.CP1.value,
        "gender": Gender.MALE.value,
        "count": 5,
        "source": EnrollmentSource.CENSUS_DECLARED.value,
    }
    # TEACHER → 403
    teacher_headers = await auth_headers(
        UserRole.TEACHER,
        email="teacher-record@test.local",
        schoolId=enroll_ctx["school"].id,
    )
    r1 = await client.post(
        "/api/enrollment", json=payload, headers=teacher_headers
    )
    assert r1.status_code == 403, r1.text

    # CENSUS_AGENT → 201 (avec un payload différent pour éviter la collision
    # avec une éventuelle row pré-existante).
    payload2 = {**payload, "count": 7, "classLevel": EnrollmentClassLevel.CP2.value}
    agent_headers = await auth_headers(
        UserRole.CENSUS_AGENT,
        email="agent-record@test.local",
        schoolId=enroll_ctx["school"].id,
    )
    r2 = await client.post(
        "/api/enrollment", json=payload2, headers=agent_headers
    )
    assert r2.status_code == 201, r2.text


# ===========================================================================
# 14. RBAC — SCHOOL_DIRECTOR ne voit que son école via aggregate
# ===========================================================================
async def test_school_director_can_only_see_own_school(
    client: AsyncClient,
    db_session: AsyncSession,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    factories.bind(db_session)
    other_school = await factories.SchoolFactory.create_async(
        regionId=enroll_ctx["region"].id,
    )
    db_session.add_all([
        Enrollment(
            schoolYearId=enroll_ctx["year"].id,
            schoolId=enroll_ctx["school"].id,
            classLevel=EnrollmentClassLevel.CP1,
            gender=Gender.FEMALE,
            count=10,
            source=EnrollmentSource.CENSUS_DECLARED,
            recordedAt=datetime.now(UTC),
        ),
        Enrollment(
            schoolYearId=enroll_ctx["year"].id,
            schoolId=other_school.id,
            classLevel=EnrollmentClassLevel.CP1,
            gender=Gender.FEMALE,
            count=99,
            source=EnrollmentSource.CENSUS_DECLARED,
            recordedAt=datetime.now(UTC),
        ),
    ])
    await db_session.flush()

    director_headers = await auth_headers(
        UserRole.SCHOOL_DIRECTOR,
        email="director@test.local",
        schoolId=enroll_ctx["school"].id,
    )
    r = await client.get(
        "/api/enrollment/aggregate",
        params={
            "scope": "SCHOOL",
            "schoolYearId": enroll_ctx["year"].id,
        },
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Seule l'école du directeur (count=10) doit apparaître.
    assert body["total"] == 10


# ===========================================================================
# 15. RBAC — REGIONAL_ADMIN respecte son scope sur aggregate
# ===========================================================================
async def test_aggregate_respects_territorial_scope_for_regional_admin(
    client: AsyncClient,
    db_session: AsyncSession,
    enroll_ctx: dict[str, Any],
    auth_headers,
) -> None:
    factories.bind(db_session)
    other_region = await factories.RegionFactory.create_async()
    other_school = await factories.SchoolFactory.create_async(
        regionId=other_region.id,
    )
    db_session.add_all([
        Enrollment(
            schoolYearId=enroll_ctx["year"].id,
            schoolId=enroll_ctx["school"].id,
            classLevel=EnrollmentClassLevel.CP1,
            gender=Gender.FEMALE,
            count=12,
            source=EnrollmentSource.CENSUS_DECLARED,
            recordedAt=datetime.now(UTC),
        ),
        Enrollment(
            schoolYearId=enroll_ctx["year"].id,
            schoolId=other_school.id,
            classLevel=EnrollmentClassLevel.CP1,
            gender=Gender.FEMALE,
            count=88,
            source=EnrollmentSource.CENSUS_DECLARED,
            recordedAt=datetime.now(UTC),
        ),
    ])
    await db_session.flush()

    regional_headers = await auth_headers(
        UserRole.REGIONAL_ADMIN,
        email="reg-scope@test.local",
        regionId=enroll_ctx["region"].id,
    )
    r = await client.get(
        "/api/enrollment/aggregate",
        params={
            "scope": "REGIONAL",
            "schoolYearId": enroll_ctx["year"].id,
        },
        headers=regional_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # REGIONAL_ADMIN ne voit pas l'école de l'autre région.
    assert body["total"] == 12


# ===========================================================================
# 16. Unique constraint — IntegrityError au niveau ORM
# ===========================================================================
async def test_unique_constraint_enforces_no_duplicate(
    db_session: AsyncSession,
    enroll_ctx: dict[str, Any],
) -> None:
    now = datetime.now(UTC)
    row1 = Enrollment(
        schoolYearId=enroll_ctx["year"].id,
        schoolId=enroll_ctx["school"].id,
        classLevel=EnrollmentClassLevel.CM2,
        gender=Gender.MALE,
        count=42,
        source=EnrollmentSource.CENSUS_DECLARED,
        recordedAt=now,
    )
    db_session.add(row1)
    await db_session.flush()

    row2 = Enrollment(
        schoolYearId=enroll_ctx["year"].id,
        schoolId=enroll_ctx["school"].id,
        classLevel=EnrollmentClassLevel.CM2,
        gender=Gender.MALE,
        count=99,  # même clé unique → doit échouer
        source=EnrollmentSource.CENSUS_DECLARED,
        recordedAt=now,
    )
    db_session.add(row2)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# ===========================================================================
# 17. (bonus) Service direct — bulk respecte aussi le max 200
# ===========================================================================
async def test_service_bulk_record_raises_on_oversized_payload(
    db_session: AsyncSession,
    enroll_ctx: dict[str, Any],
) -> None:
    factories.bind(db_session)
    # Construit un fake admin user.
    from app.modules.auth.models import User
    actor = User(
        id=generate_cuid(),
        email="svc-actor@test.local",
        passwordHash="x",
        fullName="actor",
        role=UserRole.NATIONAL_ADMIN,
        isActive=True,
    )
    db_session.add(actor)
    await db_session.flush()

    svc = EnrollmentService(db_session)
    items = []  # 201 placeholders — déclenche la validation côté service
    from app.modules.enrollment.schemas import EnrollmentCreate
    for i in range(201):
        items.append(
            EnrollmentCreate(
                schoolYearId=enroll_ctx["year"].id,
                schoolId=enroll_ctx["school"].id,
                classLevel=EnrollmentClassLevel.CP1,
                gender=Gender.FEMALE,
                count=i,
                source=EnrollmentSource.CENSUS_DECLARED,
            )
        )
    from app.core.exceptions import ValidationFailedError
    with pytest.raises(ValidationFailedError):
        await svc.bulk_record(items, actor)
