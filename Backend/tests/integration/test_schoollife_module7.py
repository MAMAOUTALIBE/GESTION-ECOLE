"""Module 7 — schoollife (discipline / health / meals / transport).

Couvre ~25 cas répartis sur 4 sous-domaines :
* Discipline (6) : create incident, list, by-student, update sanction/status,
  stats agrégées, RBAC (TEACHER refusé).
* Health (6) : create visit, create vaccination, list vaccinations by student,
  create allergy + list by student, RBAC (NATIONAL admin OK).
* Meals (6) : POST menu (auto-create MealService), GET menu/{date}, bulk
  attendance, idempotence bulk (re-saisie), stats agrégées, TEACHER autorisé
  pour présence.
* Transport (7) : create route, create stop, list stops, subscribe student,
  students_by_route, list subscriptions, RBAC école-différente refusé.

Toutes les écritures exigent que la session de test soit visible côté API
(``client`` fixture override ``get_session``).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.schoollife.enums import (
    AllergyCategory,
    AllergySeverity,
    BusSubscriptionStatus,
    IncidentStatus,
    MealAttendanceStatus,
    VaccinationStatus,
)
from app.modules.schoollife.models import (
    BusRoute,
    BusStop,
    Incident,
    MealAttendance,
    MealMenu,
    MealService,
    StudentAllergy,
    StudentBusSubscription,
    Vaccination,
)
from app.shared.enums import (
    HealthVisitType,
    IncidentSanction,
    IncidentSeverity,
    IncidentType,
    MealServiceType,
    UserRole,
)
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixture commune : un arbre territorial + 1 école + 3 élèves + 1 directeur.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def school_ctx(db_session: AsyncSession) -> dict[str, Any]:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    school = tree["school"]
    students = []
    for _ in range(3):
        s = await factories.StudentFactory.create_async(schoolId=school.id)
        students.append(s)
    return {
        "region": tree["region"],
        "prefecture": tree["prefecture"],
        "subPrefecture": tree["subPrefecture"],
        "school": school,
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


# ===========================================================================
# 1. DISCIPLINE
# ===========================================================================
@pytest.mark.asyncio
async def test_create_incident_201(
    client: AsyncClient, school_ctx: dict[str, Any],
    director_headers: dict[str, str],
) -> None:
    payload = {
        "schoolId": school_ctx["school"].id,
        "studentId": school_ctx["students"][0].id,
        "type": IncidentType.FIGHTING.value,
        "severity": IncidentSeverity.HIGH.value,
        "description": "Bagarre cour de récré entre deux élèves",
        "sanction": IncidentSanction.SUSPENSION.value,
        "occurredAt": datetime.now(UTC).isoformat(),
    }
    r = await client.post(
        "/api/schoollife/discipline/incidents",
        json=payload, headers=director_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == IncidentStatus.OPEN.value
    assert body["severity"] == IncidentSeverity.HIGH.value
    assert body["studentId"] == school_ctx["students"][0].id


@pytest.mark.asyncio
async def test_list_incidents_returns_in_school_scope(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
) -> None:
    # Seed 2 incidents en DB direct
    factories.bind(db_session)
    for severity in (IncidentSeverity.LOW, IncidentSeverity.MEDIUM):
        i = Incident(
            schoolId=school_ctx["school"].id,
            studentId=school_ctx["students"][0].id,
            type=IncidentType.LATENESS, severity=severity,
            description="Retard", sanction=IncidentSanction.WARNING,
            occurredAt=datetime.now(UTC),
        )
        db_session.add(i)
    await db_session.flush()

    r = await client.get(
        "/api/schoollife/discipline/incidents",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) >= 2


@pytest.mark.asyncio
async def test_incidents_by_student(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
) -> None:
    factories.bind(db_session)
    student = school_ctx["students"][1]
    db_session.add(Incident(
        schoolId=school_ctx["school"].id, studentId=student.id,
        type=IncidentType.BULLYING, severity=IncidentSeverity.MEDIUM,
        description="Brimades", sanction=IncidentSanction.PARENT_MEETING,
        occurredAt=datetime.now(UTC),
    ))
    await db_session.flush()

    r = await client.get(
        f"/api/schoollife/discipline/incidents/by-student/{student.id}",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()
    assert all(i["studentId"] == student.id for i in items)
    assert len(items) >= 1


@pytest.mark.asyncio
async def test_patch_incident_updates_sanction_status(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
) -> None:
    factories.bind(db_session)
    inc = Incident(
        schoolId=school_ctx["school"].id,
        studentId=school_ctx["students"][0].id,
        type=IncidentType.OTHER, severity=IncidentSeverity.LOW,
        description="Test patch", sanction=IncidentSanction.NONE,
        occurredAt=datetime.now(UTC),
    )
    db_session.add(inc)
    await db_session.flush()

    r = await client.patch(
        f"/api/schoollife/discipline/incidents/{inc.id}",
        json={
            "sanction": IncidentSanction.DETENTION.value,
            "status": IncidentStatus.RESOLVED.value,
        },
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sanction"] == IncidentSanction.DETENTION.value
    assert body["status"] == IncidentStatus.RESOLVED.value


@pytest.mark.asyncio
async def test_discipline_stats_aggregates(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
) -> None:
    factories.bind(db_session)
    for sev in (IncidentSeverity.LOW, IncidentSeverity.LOW, IncidentSeverity.HIGH):
        db_session.add(Incident(
            schoolId=school_ctx["school"].id,
            studentId=school_ctx["students"][0].id,
            type=IncidentType.LATENESS, severity=sev,
            description="Retard", sanction=IncidentSanction.WARNING,
            occurredAt=datetime.now(UTC),
        ))
    await db_session.flush()

    r = await client.get(
        "/api/schoollife/discipline/incidents/stats",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 3
    assert body["bySeverity"].get(IncidentSeverity.LOW.value, 0) >= 2
    assert body["bySeverity"].get(IncidentSeverity.HIGH.value, 0) >= 1


@pytest.mark.asyncio
async def test_discipline_rbac_teacher_forbidden_write(
    client: AsyncClient, school_ctx: dict[str, Any],
    teacher_headers: dict[str, str],
) -> None:
    payload = {
        "schoolId": school_ctx["school"].id,
        "type": IncidentType.OTHER.value,
        "description": "Tentative",
        "occurredAt": datetime.now(UTC).isoformat(),
    }
    r = await client.post(
        "/api/schoollife/discipline/incidents",
        json=payload, headers=teacher_headers,
    )
    assert r.status_code == 403, r.text


# ===========================================================================
# 2. HEALTH
# ===========================================================================
@pytest.mark.asyncio
async def test_create_health_visit_201(
    client: AsyncClient, school_ctx: dict[str, Any],
    director_headers: dict[str, str],
) -> None:
    payload = {
        "schoolId": school_ctx["school"].id,
        "studentId": school_ctx["students"][0].id,
        "type": HealthVisitType.CHECKUP.value,
        "description": "Visite médicale annuelle, RAS",
        "visitDate": date.today().isoformat(),
    }
    r = await client.post(
        "/api/schoollife/health/visits",
        json=payload, headers=director_headers,
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_create_vaccination_and_list(
    client: AsyncClient, school_ctx: dict[str, Any],
    director_headers: dict[str, str],
) -> None:
    student = school_ctx["students"][0]
    payload = {
        "studentId": student.id,
        "vaccine": "BCG",
        "dateAdministered": date.today().isoformat(),
        "batchNumber": "BCG-2026-12",
        "administeredBy": "Dr Diallo",
        "status": VaccinationStatus.ADMINISTERED.value,
    }
    r = await client.post(
        "/api/schoollife/health/vaccinations",
        json=payload, headers=director_headers,
    )
    assert r.status_code == 201, r.text
    vid = r.json()["id"]

    r = await client.get(
        f"/api/schoollife/health/vaccinations?studentId={student.id}",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()
    assert any(it["id"] == vid for it in items)


@pytest.mark.asyncio
async def test_create_allergy_and_list_by_student(
    client: AsyncClient, school_ctx: dict[str, Any],
    director_headers: dict[str, str],
) -> None:
    student = school_ctx["students"][1]
    r = await client.post(
        "/api/schoollife/health/allergies",
        json={
            "studentId": student.id,
            "allergen": "Arachides",
            "category": AllergyCategory.FOOD.value,
            "severity": AllergySeverity.SEVERE.value,
            "notes": "Auto-injecteur en infirmerie",
        },
        headers=director_headers,
    )
    assert r.status_code == 201, r.text

    r = await client.get(
        f"/api/schoollife/health/allergies/by-student/{student.id}",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) >= 1
    assert items[0]["allergen"] == "Arachides"


@pytest.mark.asyncio
async def test_health_inspector_can_read_visits(
    client: AsyncClient, db_session: AsyncSession,
    auth_headers: Any, school_ctx: dict[str, Any],
) -> None:
    inspector_headers = await auth_headers(
        UserRole.INSPECTOR, regionId=school_ctx["region"].id,
    )
    r = await client.get(
        "/api/schoollife/health/visits", headers=inspector_headers,
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_health_teacher_forbidden_write(
    client: AsyncClient, school_ctx: dict[str, Any],
    teacher_headers: dict[str, str],
) -> None:
    r = await client.post(
        "/api/schoollife/health/visits",
        json={
            "schoolId": school_ctx["school"].id,
            "type": HealthVisitType.ILLNESS.value,
            "description": "Test",
            "visitDate": date.today().isoformat(),
        },
        headers=teacher_headers,
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_vaccination_unknown_student_404(
    client: AsyncClient, director_headers: dict[str, str],
) -> None:
    r = await client.post(
        "/api/schoollife/health/vaccinations",
        json={
            "studentId": "nonexistent-id-xxxxxxx",
            "vaccine": "BCG",
            "dateAdministered": date.today().isoformat(),
        },
        headers=director_headers,
    )
    assert r.status_code == 404, r.text


# ===========================================================================
# 3. MEALS (cantines)
# ===========================================================================
@pytest.mark.asyncio
async def test_create_menu_auto_creates_meal_service(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
) -> None:
    today = date.today().isoformat()
    r = await client.post(
        "/api/schoollife/meals/menu",
        json={
            "schoolId": school_ctx["school"].id,
            "mealDate": today,
            "mealType": MealServiceType.LUNCH.value,
            "items": ["Riz au poisson", "Banane"],
            "allergens": ["Poisson"],
            "estimatedCostGNF": 2500.0,
        },
        headers=director_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["items"] == ["Riz au poisson", "Banane"]

    # MealService auto-créé
    ms = (await db_session.execute(
        select(MealService).where(
            MealService.schoolId == school_ctx["school"].id,
        )
    )).scalars().all()
    assert len(ms) >= 1


@pytest.mark.asyncio
async def test_get_menu_by_date(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
) -> None:
    today = date.today().isoformat()
    # Create menu first
    await client.post(
        "/api/schoollife/meals/menu",
        json={
            "schoolId": school_ctx["school"].id,
            "mealDate": today,
            "mealType": MealServiceType.LUNCH.value,
            "items": ["Soupe"],
        },
        headers=director_headers,
    )

    r = await client.get(
        f"/api/schoollife/meals/menu/{today}?schoolId={school_ctx['school'].id}",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) >= 1


@pytest.mark.asyncio
async def test_bulk_meal_attendance(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
    teacher_headers: dict[str, str],
) -> None:
    # Need a MealService
    factories.bind(db_session)
    ms = MealService(
        schoolId=school_ctx["school"].id,
        type=MealServiceType.LUNCH,
        serviceDate=date.today(),
        mealsPlanned=3, mealsServed=0,
        costPerMealGNF=2500.0,
    )
    db_session.add(ms)
    await db_session.flush()

    entries = [
        {"studentId": s.id, "status": MealAttendanceStatus.PRESENT.value}
        for s in school_ctx["students"]
    ]
    r = await client.post(
        "/api/schoollife/meals/attendance",
        json={"mealServiceId": ms.id, "entries": entries},
        headers=teacher_headers,
    )
    assert r.status_code == 201, r.text
    assert len(r.json()) == 3


@pytest.mark.asyncio
async def test_bulk_attendance_idempotent_resubmission(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], teacher_headers: dict[str, str],
) -> None:
    factories.bind(db_session)
    ms = MealService(
        schoolId=school_ctx["school"].id,
        type=MealServiceType.BREAKFAST,
        serviceDate=date.today(),
        mealsPlanned=2, mealsServed=0,
        costPerMealGNF=1500.0,
    )
    db_session.add(ms)
    await db_session.flush()

    student = school_ctx["students"][0]
    # First submission: PRESENT
    r1 = await client.post(
        "/api/schoollife/meals/attendance",
        json={
            "mealServiceId": ms.id,
            "entries": [{"studentId": student.id, "status": "PRESENT"}],
        },
        headers=teacher_headers,
    )
    assert r1.status_code == 201
    # Re-submission: ABSENT — must replace (no unique violation)
    r2 = await client.post(
        "/api/schoollife/meals/attendance",
        json={
            "mealServiceId": ms.id,
            "entries": [{"studentId": student.id, "status": "ABSENT"}],
        },
        headers=teacher_headers,
    )
    assert r2.status_code == 201, r2.text
    body = r2.json()
    assert body[0]["status"] == "ABSENT"


@pytest.mark.asyncio
async def test_meal_attendance_stats(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
    teacher_headers: dict[str, str],
) -> None:
    factories.bind(db_session)
    ms = MealService(
        schoolId=school_ctx["school"].id,
        type=MealServiceType.LUNCH,
        serviceDate=date.today(),
        mealsPlanned=3, mealsServed=0,
        costPerMealGNF=2500.0,
    )
    db_session.add(ms)
    await db_session.flush()

    entries = [
        {"studentId": school_ctx["students"][0].id, "status": "PRESENT"},
        {"studentId": school_ctx["students"][1].id, "status": "PRESENT"},
        {"studentId": school_ctx["students"][2].id, "status": "ABSENT"},
    ]
    await client.post(
        "/api/schoollife/meals/attendance",
        json={"mealServiceId": ms.id, "entries": entries},
        headers=teacher_headers,
    )

    r = await client.get(
        f"/api/schoollife/meals/attendance/stats?mealServiceId={ms.id}",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["present"] == 2
    assert body["absent"] == 1
    assert body["totalRecorded"] == 3
    assert body["totalPlanned"] == 3


@pytest.mark.asyncio
async def test_meal_attendance_teacher_can_write(
    client: AsyncClient, teacher_headers: dict[str, str],
) -> None:
    # Smoke : teacher peut POSTer (la précondition mealService est absente,
    # donc 404, mais pas 403). Confirme que le RBAC accepte TEACHER.
    r = await client.post(
        "/api/schoollife/meals/attendance",
        json={
            "mealServiceId": "nonexistent",
            "entries": [{"studentId": "x", "status": "PRESENT"}],
        },
        headers=teacher_headers,
    )
    assert r.status_code != 403, r.text


# ===========================================================================
# 4. TRANSPORT
# ===========================================================================
@pytest.mark.asyncio
async def test_create_route_201(
    client: AsyncClient, school_ctx: dict[str, Any],
    director_headers: dict[str, str],
) -> None:
    r = await client.post(
        "/api/schoollife/transport/routes",
        json={
            "schoolId": school_ctx["school"].id,
            "name": "Ligne A — Centre ville",
            "capacity": 50,
            "departureTime": "07:30",
            "returnTime": "16:30",
            "driverName": "Sékou Camara",
        },
        headers=director_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Ligne A — Centre ville"


@pytest.mark.asyncio
async def test_create_stop_and_list(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
) -> None:
    factories.bind(db_session)
    route = BusRoute(
        schoolId=school_ctx["school"].id,
        name="Ligne TEST stops",
        capacity=40, departureTime="07:00", returnTime="17:00",
    )
    db_session.add(route)
    await db_session.flush()

    for i, name in enumerate(("Marché", "Mairie", "École")):
        r = await client.post(
            "/api/schoollife/transport/stops",
            json={
                "routeId": route.id, "name": name,
                "lat": 9.5 + i * 0.01, "lon": -13.7,
                "pickupTime": f"07:{10 + i * 5:02d}",
                "stopOrder": i,
            },
            headers=director_headers,
        )
        assert r.status_code == 201, r.text

    r = await client.get(
        f"/api/schoollife/transport/stops?routeId={route.id}",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    stops = r.json()
    assert len(stops) == 3
    assert stops[0]["name"] == "Marché"  # stopOrder=0


@pytest.mark.asyncio
async def test_subscribe_student_and_list_route_students(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
) -> None:
    factories.bind(db_session)
    route = BusRoute(
        schoolId=school_ctx["school"].id,
        name="Ligne SUB", capacity=40,
        departureTime="07:00", returnTime="17:00",
    )
    db_session.add(route)
    await db_session.flush()

    student = school_ctx["students"][0]
    r = await client.post(
        "/api/schoollife/transport/subscriptions",
        json={
            "studentId": student.id,
            "routeId": route.id,
            "startDate": date.today().isoformat(),
            "monthlyFeeGNF": 50000,
        },
        headers=director_headers,
    )
    assert r.status_code == 201, r.text

    r = await client.get(
        f"/api/schoollife/transport/routes/{route.id}/students",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totalActiveSubscriptions"] == 1
    assert body["students"][0]["id"] == student.id


@pytest.mark.asyncio
async def test_subscribe_route_other_school_rejected(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
) -> None:
    """Élève d'une école différente que la route → 422."""
    factories.bind(db_session)
    other_tree = await factories.make_territorial_tree()
    other_school = other_tree["school"]
    other_student = await factories.StudentFactory.create_async(
        schoolId=other_school.id,
    )

    route = BusRoute(
        schoolId=school_ctx["school"].id,
        name="Ligne OTHER", capacity=40,
        departureTime="07:00", returnTime="17:00",
    )
    db_session.add(route)
    await db_session.flush()

    r = await client.post(
        "/api/schoollife/transport/subscriptions",
        json={
            "studentId": other_student.id,
            "routeId": route.id,
            "startDate": date.today().isoformat(),
        },
        headers=director_headers,
    )
    # Soit 422 (validation), soit 404 (scope) — les deux refusent.
    assert r.status_code in (422, 404), r.text


@pytest.mark.asyncio
async def test_list_subscriptions_filters_by_route(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], director_headers: dict[str, str],
) -> None:
    factories.bind(db_session)
    route = BusRoute(
        schoolId=school_ctx["school"].id,
        name="Ligne LIST", capacity=40,
        departureTime="07:00", returnTime="17:00",
    )
    db_session.add(route)
    await db_session.flush()

    for s in school_ctx["students"][:2]:
        db_session.add(StudentBusSubscription(
            studentId=s.id, routeId=route.id,
            startDate=date.today(),
            status=BusSubscriptionStatus.ACTIVE,
        ))
    await db_session.flush()

    r = await client.get(
        f"/api/schoollife/transport/subscriptions?routeId={route.id}",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 2


@pytest.mark.asyncio
async def test_route_unknown_returns_404(
    client: AsyncClient, director_headers: dict[str, str],
) -> None:
    r = await client.get(
        "/api/schoollife/transport/routes/xxxxxxxxxxxxxxxxxx/students",
        headers=director_headers,
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_transport_teacher_forbidden_write(
    client: AsyncClient, school_ctx: dict[str, Any],
    teacher_headers: dict[str, str],
) -> None:
    r = await client.post(
        "/api/schoollife/transport/routes",
        json={
            "schoolId": school_ctx["school"].id,
            "name": "Ligne X", "capacity": 40,
            "departureTime": "07:00", "returnTime": "17:00",
        },
        headers=teacher_headers,
    )
    assert r.status_code == 403, r.text


# ===========================================================================
# 5. CROSS-CUT — National admin voit tout
# ===========================================================================
@pytest.mark.asyncio
async def test_national_admin_sees_all_incidents(
    client: AsyncClient, db_session: AsyncSession,
    school_ctx: dict[str, Any], national_headers: dict[str, str],
) -> None:
    factories.bind(db_session)
    db_session.add(Incident(
        schoolId=school_ctx["school"].id,
        studentId=school_ctx["students"][0].id,
        type=IncidentType.OTHER, severity=IncidentSeverity.LOW,
        description="National read test", sanction=IncidentSanction.NONE,
        occurredAt=datetime.now(UTC),
    ))
    await db_session.flush()

    r = await client.get(
        "/api/schoollife/discipline/incidents",
        headers=national_headers,
    )
    assert r.status_code == 200, r.text
    assert any(
        i["description"] == "National read test" for i in r.json()
    )
