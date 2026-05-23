"""Phase 11 contract tests — Finance & Budget.

OpenAPI surface, Pydantic validation, gates 401, ENUMs.
Les paths DB-bound (création réelle de Budget/Expense, agrégations) restent
dans tests/integration/ (suit le même découpage que Phase 10).
"""
import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.modules.finance.schemas import (
    CreateBudgetRequest,
    CreateExpenseRequest,
    UpdateBudgetRequest,
    UpdateExpenseStatusRequest,
    UpsertUnitCostRequest,
)
from app.shared.enums import (
    BudgetCategory,
    BudgetStatus,
    ExpenseStatus,
    PolicyUnitCostCode,
)


# =====================================================================
# OpenAPI : tous les endpoints Phase 11 visibles
# =====================================================================
@pytest.mark.asyncio
async def test_openapi_exposes_phase11_endpoints(async_client: AsyncClient) -> None:
    response = await async_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    for url in (
        "/api/finance/budgets",
        "/api/finance/budgets/stats",
        "/api/finance/budgets/{budget_id}",
        "/api/finance/expenses",
        "/api/finance/expenses/{expense_id}",
        "/api/finance/unit-costs",
    ):
        assert url in paths, f"Missing endpoint: {url}"


# =====================================================================
# Phase 11 : nouveaux ENUMs disponibles et complets
# =====================================================================
def test_budget_status_values() -> None:
    assert {s.value for s in BudgetStatus} == {
        "DRAFT", "APPROVED", "ACTIVE", "CLOSED",
    }


def test_budget_category_values() -> None:
    assert {c.value for c in BudgetCategory} == {
        "SALARIES", "INFRASTRUCTURE", "EQUIPMENT", "OPERATIONS",
        "TRAINING", "TRANSPORT", "MEALS", "MISC",
    }


def test_expense_status_lifecycle() -> None:
    assert {s.value for s in ExpenseStatus} == {
        "PENDING", "APPROVED", "REJECTED", "PAID",
    }


def test_policy_unit_cost_codes_complete() -> None:
    assert {c.value for c in PolicyUnitCostCode} == {
        "NEW_SCHOOL", "NEW_CLASSROOM", "TEACHER_YEAR",
        "GIRLS_TOILETS", "ELECTRICITY_SOLAR", "WATER_BOREHOLE",
    }


# =====================================================================
# Budgets — Pydantic validation
# =====================================================================
def test_create_budget_minimum() -> None:
    dto = CreateBudgetRequest(
        fiscalYear=2026,
        category=BudgetCategory.SALARIES,
        amountPlanned=1_000_000.0,
    )
    assert dto.currency == "GNF"
    assert dto.regionId is None


def test_create_budget_rejects_zero_amount() -> None:
    with pytest.raises(ValidationError):
        CreateBudgetRequest(
            fiscalYear=2026,
            category=BudgetCategory.SALARIES,
            amountPlanned=0.0,
        )


def test_create_budget_rejects_invalid_year() -> None:
    with pytest.raises(ValidationError):
        CreateBudgetRequest(
            fiscalYear=1999,
            category=BudgetCategory.SALARIES,
            amountPlanned=10.0,
        )


def test_create_budget_rejects_bad_currency_length() -> None:
    with pytest.raises(ValidationError):
        CreateBudgetRequest(
            fiscalYear=2026,
            category=BudgetCategory.SALARIES,
            amountPlanned=10.0,
            currency="GN",  # 2 chars
        )


def test_update_budget_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        UpdateBudgetRequest()


def test_update_budget_accepts_single_field() -> None:
    dto = UpdateBudgetRequest(status=BudgetStatus.ACTIVE)
    assert dto.amountPlanned is None
    assert dto.notes is None


# =====================================================================
# Expenses — Pydantic validation
# =====================================================================
def test_create_expense_minimum() -> None:
    from datetime import date as _date
    dto = CreateExpenseRequest(
        category=BudgetCategory.OPERATIONS,
        amount=500.0,
        description="Achat fournitures",
        expenseDate=_date(2026, 5, 1),
    )
    assert dto.budgetId is None
    assert dto.currency == "GNF"


def test_create_expense_rejects_short_description() -> None:
    from datetime import date as _date
    with pytest.raises(ValidationError):
        CreateExpenseRequest(
            category=BudgetCategory.OPERATIONS,
            amount=10.0,
            description="x",  # < 3 chars
            expenseDate=_date(2026, 5, 1),
        )


def test_create_expense_rejects_negative_amount() -> None:
    from datetime import date as _date
    with pytest.raises(ValidationError):
        CreateExpenseRequest(
            category=BudgetCategory.OPERATIONS,
            amount=-5.0,
            description="Test refusé",
            expenseDate=_date(2026, 5, 1),
        )


def test_update_expense_status_requires_status() -> None:
    with pytest.raises(ValidationError):
        UpdateExpenseStatusRequest.model_validate({})


# =====================================================================
# PolicyUnitCost — validation
# =====================================================================
def test_upsert_unit_cost_minimum() -> None:
    dto = UpsertUnitCostRequest(
        code=PolicyUnitCostCode.NEW_SCHOOL,
        label="Nouvelle école",
        amount=120_000.0,
    )
    assert dto.currency == "USD"
    assert dto.isActive is True


def test_upsert_unit_cost_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        UpsertUnitCostRequest(
            code=PolicyUnitCostCode.NEW_SCHOOL,
            label="X",
            amount=-1.0,
        )


# =====================================================================
# Gates 401 — endpoints en lecture
# =====================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "/api/finance/budgets",
    "/api/finance/budgets/stats",
    "/api/finance/budgets/some-id",
    "/api/finance/expenses",
    "/api/finance/unit-costs",
])
async def test_finance_get_endpoints_require_bearer(
    async_client: AsyncClient, url: str
) -> None:
    response = await async_client.get(url)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_budget_requires_bearer(async_client: AsyncClient) -> None:
    response = await async_client.post(
        "/api/finance/budgets",
        json={
            "fiscalYear": 2026,
            "category": "SALARIES",
            "amountPlanned": 1000.0,
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_expense_requires_bearer(async_client: AsyncClient) -> None:
    response = await async_client.post(
        "/api/finance/expenses",
        json={
            "category": "OPERATIONS",
            "amount": 100.0,
            "description": "Achat",
            "expenseDate": "2026-05-01",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_upsert_unit_cost_requires_bearer(async_client: AsyncClient) -> None:
    response = await async_client.put(
        "/api/finance/unit-costs",
        json={
            "code": "NEW_SCHOOL",
            "label": "Test",
            "amount": 1.0,
        },
    )
    assert response.status_code == 401
