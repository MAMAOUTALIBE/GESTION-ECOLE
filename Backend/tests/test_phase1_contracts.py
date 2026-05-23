"""Phase 1 contract tests — verify the API surface exposed by the routers
without requiring a live database. Integration tests that hit Postgres live
in tests/integration/ (added in Phase 2 once seed data is available).
"""
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.modules.auth.schemas import LoginRequest, LoginResponse, MeResponse
from app.modules.territory.schemas import (
    CreatePrefectureRequest,
    CreateSubPrefectureRequest,
)


# ---------------------------------------------------------------------------
# OpenAPI: every endpoint we just wired must be discoverable
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_openapi_exposes_phase1_endpoints(async_client: AsyncClient) -> None:
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    # auth
    assert "/api/auth/login" in paths
    assert "post" in paths["/api/auth/login"]
    assert "/api/auth/me" in paths
    assert "get" in paths["/api/auth/me"]

    # territory
    assert "/api/territory/regions" in paths
    assert "get" in paths["/api/territory/regions"]
    assert "/api/territory/prefectures" in paths
    assert {"get", "post"}.issubset(paths["/api/territory/prefectures"].keys())
    assert "/api/territory/sub-prefectures" in paths
    assert {"get", "post"}.issubset(paths["/api/territory/sub-prefectures"].keys())


# ---------------------------------------------------------------------------
# Pydantic input validation
# ---------------------------------------------------------------------------
def test_login_request_rejects_short_password() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email="admin@scolarite.gov.gn", password="abc")


def test_login_request_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        LoginRequest(email="not-an-email", password="Admin@2026")


def test_login_request_strips_whitespace() -> None:
    dto = LoginRequest(email="  admin@scolarite.gov.gn  ", password="Admin@2026")
    assert dto.email == "admin@scolarite.gov.gn"


def test_create_prefecture_request_requires_min_length() -> None:
    with pytest.raises(ValidationError):
        CreatePrefectureRequest(name="A", code="A1")


def test_create_sub_prefecture_request_requires_prefecture_id() -> None:
    with pytest.raises(ValidationError):
        CreateSubPrefectureRequest.model_validate(  # type: ignore[call-arg]
            {"name": "Kassa", "code": "KAS"}
        )


# ---------------------------------------------------------------------------
# Endpoint behavior without auth — must return 401 (not 500)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_me_requires_bearer_token(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/auth/me")
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "unauthorized"
    assert body["message"] == "Missing bearer token"


@pytest.mark.asyncio
async def test_regions_requires_bearer_token(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/territory/regions")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_prefectures_requires_bearer_token(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/territory/prefectures")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_sub_prefectures_requires_bearer_token(async_client: AsyncClient) -> None:
    response = await async_client.get("/api/territory/sub-prefectures")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Response shape sanity (Pydantic model construction)
# ---------------------------------------------------------------------------
def test_login_response_serializes_minimal_payload() -> None:
    payload = {
        "accessToken": "fake.jwt.token",
        "user": {
            "id": "u1",
            "email": "admin@scolarite.gov.gn",
            "fullName": "Admin",
            "role": "NATIONAL_ADMIN",
            "region": None,
            "prefecture": None,
            "subPrefecture": None,
            "school": None,
        },
    }
    parsed = LoginResponse.model_validate(payload)
    assert parsed.accessToken == "fake.jwt.token"
    assert parsed.user.role.value == "NATIONAL_ADMIN"


def test_me_response_with_only_ids() -> None:
    payload = {
        "user": {
            "id": "u1",
            "email": "agent@scolarite.gov.gn",
            "fullName": "Agent",
            "role": "CENSUS_AGENT",
            "regionId": "r1",
            "prefectureId": "p1",
            "subPrefectureId": None,
            "schoolId": "s1",
        }
    }
    parsed = MeResponse.model_validate(payload)
    assert parsed.user.schoolId == "s1"
    assert parsed.user.subPrefectureId is None
