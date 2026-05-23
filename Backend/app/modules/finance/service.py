"""Finance service — Phase 11.

Pattern miroir d'`InspectionsService` :
- _scope_query / _assert_can_read / _assert_can_write
- audit log à chaque mutation
- ré-load après flush pour éviter les MissingGreenlet sous lazy='raise'
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.auth.models import User
from app.modules.finance.models import Budget, Expense, PolicyUnitCost
from app.modules.finance.schemas import (
    BudgetPage,
    BudgetRead,
    BudgetStats,
    CategoryBreakdown,
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
from app.modules.schools.models import School
from app.modules.workflow.models import AuditLog
from app.shared.enums import (
    BudgetCategory,
    BudgetStatus,
    ExpenseStatus,
    PolicyUnitCostCode,
    UserRole,
)
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)


class FinanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # BUDGETS
    # ==================================================================
    async def list_budgets(
        self,
        user: User,
        fiscal_year: int | None,
        category: BudgetCategory | None,
        status: BudgetStatus | None,
        school_id: str | None,
        region_id: str | None,
        page: int,
        page_size: int,
    ) -> BudgetPage:
        page = max(1, page)
        page_size = max(1, min(500, page_size))

        base = select(Budget).order_by(
            Budget.fiscalYear.desc(), Budget.createdAt.desc()
        )
        base = self._scope_budget_query(base, user)
        if fiscal_year is not None:
            base = base.where(Budget.fiscalYear == fiscal_year)
        if category is not None:
            base = base.where(Budget.category == category)
        if status is not None:
            base = base.where(Budget.status == status)
        if school_id:
            base = base.where(Budget.schoolId == school_id)
        if region_id:
            base = base.where(Budget.regionId == region_id)

        total = (
            await self.session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()

        rows = (
            await self.session.execute(
                base.offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars().all()

        spent_by_budget = await self._spent_by_budget([r.id for r in rows])
        items = [self._map_budget(b, spent_by_budget.get(b.id, 0.0)) for b in rows]
        return BudgetPage(rows=items, total=total, page=page, pageSize=page_size)

    async def get_budget(self, user: User, budget_id: str) -> BudgetRead:
        b = await self._load_budget(budget_id)
        await self._assert_can_read_budget(user, b)
        spent = await self._spent_by_budget([b.id])
        return self._map_budget(b, spent.get(b.id, 0.0))

    async def create_budget(
        self, user: User, dto: CreateBudgetRequest
    ) -> BudgetRead:
        self._assert_scope_consistent(dto.regionId, dto.prefectureId,
                                      dto.subPrefectureId, dto.schoolId)
        await self._assert_can_write_scope(
            user, dto.regionId, dto.prefectureId, dto.subPrefectureId, dto.schoolId
        )

        budget = Budget(
            fiscalYear=dto.fiscalYear,
            category=dto.category,
            status=BudgetStatus.DRAFT,
            regionId=dto.regionId,
            prefectureId=dto.prefectureId,
            subPrefectureId=dto.subPrefectureId,
            schoolId=dto.schoolId,
            amountPlanned=dto.amountPlanned,
            currency=dto.currency,
            notes=dto.notes,
            createdById=user.id,
        )
        self.session.add(budget)
        await self.session.flush()

        self.session.add(AuditLog(
            actorId=user.id,
            action="CREATE_BUDGET",
            entity="Budget",
            entityId=budget.id,
            metadata_={
                "fiscalYear": dto.fiscalYear,
                "category": dto.category.value,
                "amountPlanned": dto.amountPlanned,
            },
        ))
        await self.session.flush()
        loaded = await self._load_budget(budget.id)
        return self._map_budget(loaded, 0.0)

    async def update_budget(
        self, user: User, budget_id: str, dto: UpdateBudgetRequest
    ) -> BudgetRead:
        b = await self._load_budget(budget_id)
        await self._assert_can_write_budget(user, b)

        if b.status == BudgetStatus.CLOSED and dto.status != BudgetStatus.CLOSED:
            # Une fois clôturé, on ne ré-ouvre pas
            raise ConflictError(
                detail="Budget clôturé — modification interdite."
            )

        if dto.status is not None:
            b.status = dto.status
        if dto.amountPlanned is not None:
            b.amountPlanned = dto.amountPlanned
        if dto.notes is not None:
            b.notes = dto.notes

        self.session.add(AuditLog(
            actorId=user.id,
            action="UPDATE_BUDGET",
            entity="Budget",
            entityId=b.id,
            metadata_={"status": b.status.value},
        ))
        await self.session.flush()
        loaded = await self._load_budget(b.id)
        spent = await self._spent_by_budget([loaded.id])
        return self._map_budget(loaded, spent.get(loaded.id, 0.0))

    # ==================================================================
    # BUDGET STATS
    # ==================================================================
    async def budget_stats(
        self, user: User, fiscal_year: int | None
    ) -> BudgetStats:
        scoped = self._scope_budget_query(select(Budget.id), user).subquery()
        scoped_ids_q = select(scoped.c.id)

        budgets_stmt = select(Budget).where(Budget.id.in_(scoped_ids_q))
        if fiscal_year is not None:
            budgets_stmt = budgets_stmt.where(Budget.fiscalYear == fiscal_year)
        budgets = (await self.session.execute(budgets_stmt)).scalars().all()

        if not budgets:
            return BudgetStats(
                fiscalYear=fiscal_year,
                totalPlanned=0.0,
                totalSpent=0.0,
                totalRemaining=0.0,
                consumptionRate=0.0,
                overspendingBudgets=0,
                breakdownByCategory=[],
            )

        spent_by_budget = await self._spent_by_budget([b.id for b in budgets])

        total_planned = sum(b.amountPlanned for b in budgets)
        total_spent = sum(spent_by_budget.get(b.id, 0.0) for b in budgets)
        overspending = sum(
            1 for b in budgets
            if spent_by_budget.get(b.id, 0.0) > b.amountPlanned
        )

        per_cat: dict[BudgetCategory, dict[str, float]] = {}
        for b in budgets:
            slot = per_cat.setdefault(b.category, {"planned": 0.0, "spent": 0.0})
            slot["planned"] += b.amountPlanned
            slot["spent"] += spent_by_budget.get(b.id, 0.0)

        breakdown = [
            CategoryBreakdown(
                category=cat,
                planned=round(values["planned"], 2),
                spent=round(values["spent"], 2),
                remaining=round(values["planned"] - values["spent"], 2),
                consumptionRate=(
                    round((values["spent"] / values["planned"]) * 100, 1)
                    if values["planned"] else 0.0
                ),
            )
            for cat, values in sorted(per_cat.items(), key=lambda kv: kv[0].value)
        ]

        return BudgetStats(
            fiscalYear=fiscal_year,
            totalPlanned=round(total_planned, 2),
            totalSpent=round(total_spent, 2),
            totalRemaining=round(total_planned - total_spent, 2),
            consumptionRate=(
                round((total_spent / total_planned) * 100, 1)
                if total_planned else 0.0
            ),
            overspendingBudgets=overspending,
            breakdownByCategory=breakdown,
        )

    # ==================================================================
    # EXPENSES
    # ==================================================================
    async def list_expenses(
        self,
        user: User,
        budget_id: str | None,
        school_id: str | None,
        category: BudgetCategory | None,
        status: ExpenseStatus | None,
        page: int,
        page_size: int,
    ) -> ExpensePage:
        page = max(1, page)
        page_size = max(1, min(500, page_size))

        base = select(Expense).order_by(Expense.expenseDate.desc())
        base = self._scope_expense_query(base, user)
        if budget_id:
            base = base.where(Expense.budgetId == budget_id)
        if school_id:
            base = base.where(Expense.schoolId == school_id)
        if category is not None:
            base = base.where(Expense.category == category)
        if status is not None:
            base = base.where(Expense.status == status)

        total = (
            await self.session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()

        rows = (
            await self.session.execute(
                base.offset((page - 1) * page_size).limit(page_size)
            )
        ).scalars().all()

        return ExpensePage(
            rows=[ExpenseRead.model_validate(r) for r in rows],
            total=total, page=page, pageSize=page_size,
        )

    async def create_expense(
        self, user: User, dto: CreateExpenseRequest
    ) -> ExpenseRead:
        self._assert_scope_consistent(
            dto.regionId, dto.prefectureId, dto.subPrefectureId, dto.schoolId
        )

        # Si un budget est référencé, on s'assure de la cohérence catégorie
        # et on dérive le scope du budget si non fourni explicitement.
        budget = None
        if dto.budgetId:
            budget = await self.session.get(Budget, dto.budgetId)
            if budget is None:
                raise NotFoundError(detail="Budget introuvable")
            if budget.category != dto.category:
                raise ConflictError(
                    detail="La catégorie de la dépense ne correspond pas au budget."
                )
            if budget.status not in (BudgetStatus.APPROVED, BudgetStatus.ACTIVE):
                raise ConflictError(
                    detail="Budget non actif — dépense impossible."
                )

        # Vérification du scope de l'utilisateur sur la dépense
        await self._assert_can_write_scope(
            user, dto.regionId, dto.prefectureId, dto.subPrefectureId, dto.schoolId
        )

        # Si schoolId fourni, on enrichit les FK territoriales pour l'index
        region_id = dto.regionId
        prefecture_id = dto.prefectureId
        sub_prefecture_id = dto.subPrefectureId
        if dto.schoolId:
            school = await self.session.get(School, dto.schoolId)
            if school is None:
                raise NotFoundError(detail="École introuvable")
            region_id = region_id or school.regionId
            prefecture_id = prefecture_id or school.prefectureId
            sub_prefecture_id = sub_prefecture_id or school.subPrefectureId

        expense = Expense(
            budgetId=dto.budgetId,
            category=dto.category,
            amount=dto.amount,
            currency=dto.currency,
            description=dto.description,
            expenseDate=dto.expenseDate,
            status=ExpenseStatus.PENDING,
            schoolId=dto.schoolId,
            regionId=region_id,
            prefectureId=prefecture_id,
            subPrefectureId=sub_prefecture_id,
            receiptUrl=dto.receiptUrl,
            createdById=user.id,
        )
        self.session.add(expense)
        await self.session.flush()

        self.session.add(AuditLog(
            actorId=user.id,
            action="CREATE_EXPENSE",
            entity="Expense",
            entityId=expense.id,
            metadata_={
                "amount": dto.amount,
                "category": dto.category.value,
                "budgetId": dto.budgetId,
            },
        ))
        await self.session.flush()
        await self.session.refresh(expense)
        return ExpenseRead.model_validate(expense)

    async def update_expense_status(
        self, user: User, expense_id: str, dto: UpdateExpenseStatusRequest
    ) -> ExpenseRead:
        expense = await self.session.get(Expense, expense_id)
        if expense is None:
            raise NotFoundError(detail="Dépense introuvable")

        # Approbation = rôles de validation uniquement
        approval_roles = (
            *NATIONAL_SCOPE_ROLES, *REGIONAL_SCOPE_ROLES,
            *PREFECTURE_SCOPE_ROLES,
        )
        if dto.status in (ExpenseStatus.APPROVED, ExpenseStatus.REJECTED, ExpenseStatus.PAID):
            if user.role not in approval_roles:
                raise ForbiddenError(
                    detail="Seuls les administrateurs peuvent valider/refuser/payer."
                )
            await self._assert_can_write_scope(
                user, expense.regionId, expense.prefectureId,
                expense.subPrefectureId, expense.schoolId,
            )

        expense.status = dto.status
        if dto.status == ExpenseStatus.APPROVED:
            expense.approvedById = user.id
            expense.approvedAt = datetime.now(UTC)
        elif dto.status == ExpenseStatus.REJECTED:
            expense.approvedById = None
            expense.approvedAt = None

        self.session.add(AuditLog(
            actorId=user.id,
            action="UPDATE_EXPENSE_STATUS",
            entity="Expense",
            entityId=expense.id,
            metadata_={"status": dto.status.value, "note": dto.note},
        ))
        await self.session.flush()
        await self.session.refresh(expense)
        return ExpenseRead.model_validate(expense)

    # ==================================================================
    # POLICY UNIT COSTS
    # ==================================================================
    async def list_unit_costs(self) -> UnitCostsResponse:
        rows = (await self.session.execute(
            select(PolicyUnitCost).order_by(PolicyUnitCost.code)
        )).scalars().all()
        return UnitCostsResponse(
            rows=[UnitCostRead.model_validate(r) for r in rows]
        )

    async def upsert_unit_cost(
        self, user: User, dto: UpsertUnitCostRequest
    ) -> UnitCostRead:
        if user.role not in NATIONAL_SCOPE_ROLES:
            raise ForbiddenError(
                detail="Seuls les admins nationaux mettent à jour les coûts unitaires."
            )

        existing = (await self.session.execute(
            select(PolicyUnitCost).where(PolicyUnitCost.code == dto.code)
        )).scalar_one_or_none()

        if existing:
            existing.label = dto.label
            existing.amount = dto.amount
            existing.currency = dto.currency
            existing.source = dto.source
            existing.isActive = dto.isActive
            existing.updatedById = user.id
            cost = existing
            action = "UPDATE_UNIT_COST"
        else:
            cost = PolicyUnitCost(
                code=dto.code,
                label=dto.label,
                amount=dto.amount,
                currency=dto.currency,
                source=dto.source,
                isActive=dto.isActive,
                updatedById=user.id,
            )
            self.session.add(cost)
            action = "CREATE_UNIT_COST"

        await self.session.flush()
        self.session.add(AuditLog(
            actorId=user.id,
            action=action,
            entity="PolicyUnitCost",
            entityId=cost.id,
            metadata_={"code": dto.code.value, "amount": dto.amount},
        ))
        await self.session.flush()
        await self.session.refresh(cost)
        return UnitCostRead.model_validate(cost)

    async def get_unit_costs_map(self) -> dict[PolicyUnitCostCode, float]:
        """Retourne uniquement les coûts actifs, en map code → amount.

        Utilisé par AnalyticsService.policy_simulate pour overrider les
        valeurs Banque Mondiale par défaut.
        """
        rows = (await self.session.execute(
            select(PolicyUnitCost.code, PolicyUnitCost.amount)
            .where(PolicyUnitCost.isActive.is_(True))
        )).all()
        return {code: float(amount) for code, amount in rows}

    # ==================================================================
    # SCOPE / SECURITY HELPERS
    # ==================================================================
    @staticmethod
    def _assert_scope_consistent(
        region_id: str | None,
        prefecture_id: str | None,
        sub_prefecture_id: str | None,
        school_id: str | None,
    ) -> None:
        # Au plus un scope explicite — sinon ambiguïté.
        provided = sum(
            1 for x in (region_id, prefecture_id, sub_prefecture_id, school_id)
            if x is not None
        )
        if provided > 1 and not school_id:
            # Une école porte naturellement région+préfecture+sous-préfecture,
            # donc on tolère la combinaison "schoolId + ses parents".
            raise ConflictError(
                detail="Scope ambigu : préciser un seul niveau territorial ou une école."
            )

    async def _assert_can_write_scope(
        self,
        user: User,
        region_id: str | None,
        prefecture_id: str | None,
        sub_prefecture_id: str | None,
        school_id: str | None,
    ) -> None:
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        if user.role in REGIONAL_SCOPE_ROLES:
            if not user.regionId:
                raise ForbiddenError(detail="Région utilisateur manquante.")
            if region_id and region_id != user.regionId:
                raise ForbiddenError(detail="Hors de la région autorisée.")
            if school_id:
                school = await self.session.get(School, school_id)
                if school is None or school.regionId != user.regionId:
                    raise ForbiddenError(detail="École hors région.")
            return
        if user.role in PREFECTURE_SCOPE_ROLES:
            if not user.prefectureId:
                raise ForbiddenError(detail="Préfecture utilisateur manquante.")
            if prefecture_id and prefecture_id != user.prefectureId:
                raise ForbiddenError(detail="Hors de la préfecture autorisée.")
            if school_id:
                school = await self.session.get(School, school_id)
                if school is None or school.prefectureId != user.prefectureId:
                    raise ForbiddenError(detail="École hors préfecture.")
            return
        if user.role in SUB_PREFECTURE_SCOPE_ROLES:
            if not user.subPrefectureId:
                raise ForbiddenError(detail="Sous-préfecture utilisateur manquante.")
            if sub_prefecture_id and sub_prefecture_id != user.subPrefectureId:
                raise ForbiddenError(detail="Hors de la sous-préfecture autorisée.")
            if school_id:
                school = await self.session.get(School, school_id)
                if school is None or school.subPrefectureId != user.subPrefectureId:
                    raise ForbiddenError(detail="École hors sous-préfecture.")
            return
        # Direction d'école : ne peut écrire que sur sa propre école
        if user.role == UserRole.SCHOOL_DIRECTOR:
            if not school_id or school_id != user.schoolId:
                raise ForbiddenError(detail="Hors de l'école dirigée.")
            return
        raise ForbiddenError(detail="Rôle non autorisé pour les opérations financières.")

    def _scope_budget_query(self, stmt: Any, user: User) -> Any:
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(Budget.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(Budget.prefectureId == user.prefectureId)
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(Budget.subPrefectureId == user.subPrefectureId)
        if user.schoolId:
            return stmt.where(Budget.schoolId == user.schoolId)
        return stmt.where(Budget.id == "__none__")

    def _scope_expense_query(self, stmt: Any, user: User) -> Any:
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(Expense.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(Expense.prefectureId == user.prefectureId)
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(Expense.subPrefectureId == user.subPrefectureId)
        if user.schoolId:
            return stmt.where(Expense.schoolId == user.schoolId)
        return stmt.where(Expense.id == "__none__")

    async def _assert_can_read_budget(self, user: User, b: Budget) -> None:
        if user.role in NATIONAL_SCOPE_ROLES:
            return
        if (b.regionId and b.regionId == user.regionId) or \
           (b.prefectureId and b.prefectureId == user.prefectureId) or \
           (b.subPrefectureId and b.subPrefectureId == user.subPrefectureId) or \
           (b.schoolId and b.schoolId == user.schoolId):
            return
        raise ForbiddenError(detail="Budget hors de votre périmètre.")

    async def _assert_can_write_budget(self, user: User, b: Budget) -> None:
        await self._assert_can_write_scope(
            user, b.regionId, b.prefectureId, b.subPrefectureId, b.schoolId
        )

    async def _load_budget(self, budget_id: str) -> Budget:
        b = await self.session.get(Budget, budget_id)
        if b is None:
            raise NotFoundError(detail="Budget introuvable")
        return b

    # ==================================================================
    # AGGREGATION HELPERS
    # ==================================================================
    async def _spent_by_budget(
        self, budget_ids: list[str]
    ) -> dict[str, float]:
        if not budget_ids:
            return {}
        rows = (await self.session.execute(
            select(Expense.budgetId, func.sum(Expense.amount))
            .where(
                Expense.budgetId.in_(budget_ids),
                Expense.status.in_(
                    [ExpenseStatus.APPROVED, ExpenseStatus.PAID]
                ),
            )
            .group_by(Expense.budgetId)
        )).all()
        return {bid: float(total or 0.0) for bid, total in rows}

    @staticmethod
    def _map_budget(b: Budget, spent: float) -> BudgetRead:
        remaining = b.amountPlanned - spent
        rate = round((spent / b.amountPlanned) * 100, 1) if b.amountPlanned else 0.0
        return BudgetRead(
            id=b.id,
            fiscalYear=b.fiscalYear,
            category=b.category,
            status=b.status,
            regionId=b.regionId,
            prefectureId=b.prefectureId,
            subPrefectureId=b.subPrefectureId,
            schoolId=b.schoolId,
            amountPlanned=b.amountPlanned,
            amountSpent=round(spent, 2),
            amountRemaining=round(remaining, 2),
            consumptionRate=rate,
            currency=b.currency,
            notes=b.notes,
            createdById=b.createdById,
            createdAt=b.createdAt,
            updatedAt=b.updatedAt,
        )
