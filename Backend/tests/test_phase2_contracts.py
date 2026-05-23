"""Phase 2 contract tests — verify Schools + Census API surface and Pydantic
validation rules without touching a live database. Integration tests with
seed data come in Phase 3+.
"""
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.modules.census.schemas import (
    AssignTeacherClassesRequest,
    CreateStudentRequest,
    CreateTeacherRequest,
    DashboardQuery,
    TransferStudentRequest,
)
from app.modules.schools.schemas import (
    CreateClassRoomRequest,
    CreateSchoolRequest,
    UpdateSchoolRequest,
)
from app.shared.enums import Gender


# ---------------------------------------------------------------------
# OpenAPI: every endpoint we just wired must be discoverable
# ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_openapi_exposes_phase2_endpoints(async_client: AsyncClient) -> None:
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    # Schools
    for url in (
        "/api/schools",
        "/api/schools/{school_id}",
        "/api/classes",
        "/api/classes/{class_id}",
    ):
        assert url in paths, f"Missing endpoint: {url}"

    # Census
    for url in (
        "/api/census/dashboard",
        "/api/census/metadata",
        "/api/census/students",
        "/api/census/students/cards",
        "/api/census/students/{student_id}",
        "/api/census/students/{student_id}/class",
        "/api/census/students/{student_id}/transfer",
        "/api/census/teachers",
        "/api/census/teachers/cards",
        "/api/census/teachers/{teacher_id}",
        "/api/census/teachers/{teacher_id}/classes",
    ):
        assert url in paths, f"Missing endpoint: {url}"


# ---------------------------------------------------------------------
# Pydantic validation — Schools
# ---------------------------------------------------------------------
def test_create_school_request_strips_and_validates() -> None:
    dto = CreateSchoolRequest(
        name="  Lycée Conakry  ", code=" lk-001 ", regionId="r1",
        latitude=9.5, longitude=-13.7,
    )
    assert dto.name == "Lycée Conakry"
    # Code is NOT lowercased here — service does .upper() before persisting
    assert dto.code == "lk-001"


def test_create_school_request_rejects_invalid_lat_lon() -> None:
    with pytest.raises(ValidationError):
        CreateSchoolRequest(name="Test School", code="TS01", regionId="r1", latitude=99)
    with pytest.raises(ValidationError):
        CreateSchoolRequest(name="Test School", code="TS01", regionId="r1", longitude=-200)


def test_create_school_request_requires_min_length() -> None:
    with pytest.raises(ValidationError):
        CreateSchoolRequest(name="X", code="TS01", regionId="r1")
    with pytest.raises(ValidationError):
        CreateSchoolRequest(name="School", code="X", regionId="r1")


def test_update_school_request_all_optional() -> None:
    dto = UpdateSchoolRequest()  # No fields required
    assert dto.name is None
    assert dto.latitude is None


def test_create_class_room_request_min_length() -> None:
    with pytest.raises(ValidationError):
        CreateClassRoomRequest(name="", schoolId="s1")
    dto = CreateClassRoomRequest(name="6e A", schoolId="s1", maxStudents=40)
    assert dto.maxStudents == 40


def test_create_class_room_rejects_zero_max_students() -> None:
    with pytest.raises(ValidationError):
        CreateClassRoomRequest(name="6e A", schoolId="s1", maxStudents=0)


# ---------------------------------------------------------------------
# Pydantic validation — Census
# ---------------------------------------------------------------------
def test_create_student_request_strips_names() -> None:
    dto = CreateStudentRequest(
        firstName="  Mamadou  ", lastName="  Diallo  ", gender=Gender.MALE,
        schoolId="s1",
    )
    assert dto.firstName == "Mamadou"
    assert dto.lastName == "Diallo"
    assert dto.gender == Gender.MALE


def test_create_student_request_min_length() -> None:
    with pytest.raises(ValidationError):
        CreateStudentRequest(firstName="A", lastName="Diallo",
                             gender=Gender.MALE, schoolId="s1")


def test_create_teacher_request_with_classes() -> None:
    dto = CreateTeacherRequest(
        firstName="Aïssata", lastName="Camara", gender=Gender.FEMALE,
        schoolId="s1", classRoomIds=["c1", "c2"],
    )
    assert dto.classRoomIds == ["c1", "c2"]


def test_assign_teacher_classes_request() -> None:
    dto = AssignTeacherClassesRequest(classRoomIds=["c1"])
    assert dto.classRoomIds == ["c1"]
    # Empty list is valid (clears assignments)
    dto2 = AssignTeacherClassesRequest(classRoomIds=[])
    assert dto2.classRoomIds == []


def test_transfer_student_request_requires_to_school() -> None:
    with pytest.raises(ValidationError):
        TransferStudentRequest.model_validate({})  # type: ignore[call-arg]
    dto = TransferStudentRequest(toSchoolId="s2", toClassRoomId="c5", reason="Mutation")
    assert dto.toSchoolId == "s2"


def test_dashboard_query_optional_filters() -> None:
    q = DashboardQuery()
    assert q.regionId is None
    q2 = DashboardQuery(regionId="r1", prefecture="Kaloum")
    assert q2.prefecture == "Kaloum"


# ---------------------------------------------------------------------
# Auth-required endpoints — no token = 401, not 500
# ---------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "/api/schools",
    "/api/classes",
    "/api/census/dashboard",
    "/api/census/metadata",
    "/api/census/students",
    "/api/census/teachers",
])
async def test_phase2_endpoints_require_bearer_token(
    async_client: AsyncClient, url: str
) -> None:
    response = await async_client.get(url)
    assert response.status_code == 401, f"{url} should return 401 without auth"
    assert response.json()["code"] == "unauthorized"
