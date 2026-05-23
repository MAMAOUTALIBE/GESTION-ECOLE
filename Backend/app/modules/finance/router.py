from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.modules.auth.models import User
from app.modules.finance.schemas import (
    BudgetPage,
    BudgetRead,
    BudgetStats,
    CreateBudgetRequest,
    CreateExpenseRequest,
    ExpensePage,
    ExpenseRead,
    UnitCostRead,
    UnitCostsResponse,
    UpdateBudgetRequest,
    UpdateExpenseStatusRequest,
    UpsertUnitCostRequest,
)
from app.modules.finance.service import FinanceService
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import (
    BudgetCategory,
    BudgetStatus,
    ExpenseStatus,
    UserRole,
)
from app.shared.permissions import require_roles

# Création de budgets : niveaux administratifs (pas les enseignants)
BUDGET_WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
)
# Création de dépenses : tous les niveaux administratifs + directeur d'école
EXPENSE_WRITE_ROLES = (*BUDGET_WRITE_ROLES, UserRole.SCHOOL_DIRECTOR)
# Référentiel coûts unitaires : ministère uniquement
UNIT_COST_WRITE_ROLES = (UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN)


def _service(session: DbSession) -> FinanceService:
    return FinanceService(session)


FinSvc = Annotated[FinanceService, Depends(_service)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]

router = APIRouter(tags=["finance"])


# =============================================================
# BUDGETS
# =============================================================
@router.get(
    "/budgets",
    response_model=BudgetPage,
    summary="Lister les budgets (filtres + pagination, scope-aware)",
)
async def list_budgets(
    user: CurrentUserDep,
    service: FinSvc,
    fiscalYear: Annotated[int | None, Query(ge=2000, le=2100)] = None,
    category: Annotated[BudgetCategory | None, Query()] = None,
    status_: Annotated[BudgetStatus | None, Query(alias="status")] = None,
    schoolId: Annotated[str | None, Query()] = None,
    regionId: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=500)] = 50,
) -> BudgetPage:
    return await service.list_budgets(
        user, fiscalYear, category, status_, schoolId, regionId, page, pageSize
    )


@router.get(
    "/budgets/stats",
    response_model=BudgetStats,
    summary="Synthèse exécution budgétaire (scope-aware)",
)
async def budget_stats(
    user: CurrentUserDep,
    service: FinSvc,
    fiscalYear: Annotated[int | None, Query(ge=2000, le=2100)] = None,
) -> BudgetStats:
    return await service.budget_stats(user, fiscalYear)


@router.get(
    "/budgets/{budget_id}",
    response_model=BudgetRead,
    summary="Détail d'un budget (planifié, dépensé, reste à consommer)",
)
async def get_budget(
    budget_id: str, user: CurrentUserDep, service: FinSvc
) -> BudgetRead:
    return await service.get_budget(user, budget_id)


@router.post(
    "/budgets",
    response_model=BudgetRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*BUDGET_WRITE_ROLES))],
    summary="Créer un nouveau budget",
)
async def create_budget(
    dto: CreateBudgetRequest, user: CurrentUserDep, service: FinSvc
) -> BudgetRead:
    return await service.create_budget(user, dto)


@router.patch(
    "/budgets/{budget_id}",
    response_model=BudgetRead,
    dependencies=[Depends(require_roles(*BUDGET_WRITE_ROLES))],
    summary="MAJ d'un budget (statut, montant, notes)",
)
async def update_budget(
    budget_id: str,
    dto: UpdateBudgetRequest,
    user: CurrentUserDep,
    service: FinSvc,
) -> BudgetRead:
    return await service.update_budget(user, budget_id, dto)


# =============================================================
# EXPENSES
# =============================================================
@router.get(
    "/expenses",
    response_model=ExpensePage,
    summary="Lister les dépenses (filtres + pagination, scope-aware)",
)
async def list_expenses(
    user: CurrentUserDep,
    service: FinSvc,
    budgetId: Annotated[str | None, Query()] = None,
    schoolId: Annotated[str | None, Query()] = None,
    category: Annotated[BudgetCategory | None, Query()] = None,
    status_: Annotated[ExpenseStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=500)] = 50,
) -> ExpensePage:
    return await service.list_expenses(
        user, budgetId, schoolId, category, status_, page, pageSize
    )


@router.post(
    "/expenses",
    response_model=ExpenseRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*EXPENSE_WRITE_ROLES))],
    summary="Saisir une nouvelle dépense (en attente de validation)",
)
async def create_expense(
    dto: CreateExpenseRequest, user: CurrentUserDep, service: FinSvc
) -> ExpenseRead:
    return await service.create_expense(user, dto)


@router.patch(
    "/expenses/{expense_id}",
    response_model=ExpenseRead,
    dependencies=[Depends(require_roles(*EXPENSE_WRITE_ROLES))],
    summary="MAJ statut d'une dépense (validation/refus/paiement)",
)
async def update_expense_status(
    expense_id: str,
    dto: UpdateExpenseStatusRequest,
    user: CurrentUserDep,
    service: FinSvc,
) -> ExpenseRead:
    return await service.update_expense_status(user, expense_id, dto)


# =============================================================
# POLICY UNIT COSTS — référentiel pour le simulateur
# =============================================================
@router.get(
    "/unit-costs",
    response_model=UnitCostsResponse,
    summary="Référentiel des coûts unitaires (lecture libre)",
)
async def list_unit_costs(
    user: CurrentUserDep, service: FinSvc
) -> UnitCostsResponse:
    _ = user
    return await service.list_unit_costs()


@router.put(
    "/unit-costs",
    response_model=UnitCostRead,
    dependencies=[Depends(require_roles(*UNIT_COST_WRITE_ROLES))],
    summary="Upsert d'un coût unitaire (admins nationaux uniquement)",
)
async def upsert_unit_cost(
    dto: UpsertUnitCostRequest, user: CurrentUserDep, service: FinSvc
) -> UnitCostRead:
    return await service.upsert_unit_cost(user, dto)
