"""Pydantic schemas — Phase 11 Finance & Budget."""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.enums import (
    BudgetCategory,
    BudgetStatus,
    ExpenseStatus,
    PolicyUnitCostCode,
)


# =============================================================
# BUDGETS
# =============================================================
class _ScopeMixin(BaseModel):
    regionId: str | None = None
    prefectureId: str | None = None
    subPrefectureId: str | None = None
    schoolId: str | None = None


class CreateBudgetRequest(_ScopeMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    fiscalYear: int = Field(ge=2000, le=2100)
    category: BudgetCategory
    amountPlanned: float = Field(gt=0)
    currency: str = Field(default="GNF", min_length=3, max_length=3)
    notes: str | None = Field(default=None, max_length=2000)


class UpdateBudgetRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: BudgetStatus | None = None
    amountPlanned: float | None = Field(default=None, gt=0)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _at_least_one(self) -> "UpdateBudgetRequest":
        if self.status is None and self.amountPlanned is None and self.notes is None:
            raise ValueError("Au moins un champ doit être fourni.")
        return self


class BudgetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    fiscalYear: int
    category: BudgetCategory
    status: BudgetStatus
    regionId: str | None = None
    prefectureId: str | None = None
    subPrefectureId: str | None = None
    schoolId: str | None = None
    amountPlanned: float
    amountSpent: float = 0.0
    amountRemaining: float = 0.0
    consumptionRate: float = 0.0  # 0..100 %
    currency: str
    notes: str | None = None
    createdById: str | None = None
    createdAt: datetime
    updatedAt: datetime


class BudgetPage(BaseModel):
    rows: list[BudgetRead]
    total: int
    page: int
    pageSize: int


class CategoryBreakdown(BaseModel):
    category: BudgetCategory
    planned: float
    spent: float
    remaining: float
    consumptionRate: float


class BudgetStats(BaseModel):
    fiscalYear: int | None = None
    totalPlanned: float
    totalSpent: float
    totalRemaining: float
    consumptionRate: float
    overspendingBudgets: int  # nb de budgets dont spent > planned
    breakdownByCategory: list[CategoryBreakdown]


# =============================================================
# EXPENSES
# =============================================================
class CreateExpenseRequest(_ScopeMixin):
    model_config = ConfigDict(str_strip_whitespace=True)

    budgetId: str | None = None
    category: BudgetCategory
    amount: float = Field(gt=0)
    currency: str = Field(default="GNF", min_length=3, max_length=3)
    description: str = Field(min_length=3, max_length=2000)
    expenseDate: date
    receiptUrl: str | None = None


class UpdateExpenseStatusRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: ExpenseStatus
    note: str | None = Field(default=None, max_length=2000)


class ExpenseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    budgetId: str | None = None
    category: BudgetCategory
    amount: float
    currency: str
    description: str
    expenseDate: date
    status: ExpenseStatus
    schoolId: str | None = None
    regionId: str | None = None
    prefectureId: str | None = None
    subPrefectureId: str | None = None
    approvedById: str | None = None
    approvedAt: datetime | None = None
    receiptUrl: str | None = None
    createdById: str | None = None
    createdAt: datetime
    updatedAt: datetime


class ExpensePage(BaseModel):
    rows: list[ExpenseRead]
    total: int
    page: int
    pageSize: int


# =============================================================
# POLICY UNIT COSTS
# =============================================================
class UpsertUnitCostRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    code: PolicyUnitCostCode
    label: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    source: str | None = Field(default=None, max_length=500)
    isActive: bool = True


class UnitCostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: PolicyUnitCostCode
    label: str
    amount: float
    currency: str
    source: str | None = None
    isActive: bool
    updatedById: str | None = None
    updatedAt: datetime


class UnitCostsResponse(BaseModel):
    rows: list[UnitCostRead]
