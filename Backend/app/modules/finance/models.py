"""Finance & Budget — Phase 11.

Trois tables :
    Budget          — enveloppe planifiée par exercice fiscal × territoire × catégorie
    Expense         — dépense réelle (souvent rattachée à un Budget)
    PolicyUnitCost  — référentiel de coûts unitaires utilisé par le simulateur

Un Budget est attaché à un seul scope (national si tout est null, sinon le scope
le plus profond renseigné). Pour la performance des requêtes scope-aware, les
Expense dupliquent les FK territoriales — la cohérence est garantie au moment
de l'insertion par le service.
"""
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, TimestampMixin, cuid_pk
from app.shared.enums import (
    BudgetCategory,
    BudgetStatus,
    ExpenseStatus,
    PolicyUnitCostCode,
)

if TYPE_CHECKING:
    from app.modules.auth.models import User
    from app.modules.schools.models import School
    from app.modules.territory.models import Prefecture, Region, SubPrefecture


class Budget(Base, TimestampMixin):
    __tablename__ = "Budget"
    __table_args__ = (
        Index("ix_Budget_fiscalYear_status", "fiscalYear", "status"),
        Index("ix_Budget_regionId_fiscalYear", "regionId", "fiscalYear"),
        Index("ix_Budget_schoolId_fiscalYear", "schoolId", "fiscalYear"),
        Index("ix_Budget_category_fiscalYear", "category", "fiscalYear"),
    )

    id: Mapped[str] = cuid_pk()
    fiscalYear: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[BudgetCategory] = mapped_column(
        Enum(BudgetCategory, name="BudgetCategory", native_enum=True),
        nullable=False,
    )
    status: Mapped[BudgetStatus] = mapped_column(
        Enum(BudgetStatus, name="BudgetStatus", native_enum=True),
        default=BudgetStatus.DRAFT,
        nullable=False,
    )

    # Scope (au plus un de ces FK ; si tous null, scope national)
    regionId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Region.id"), nullable=True
    )
    prefectureId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Prefecture.id"), nullable=True
    )
    subPrefectureId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("SubPrefecture.id"), nullable=True
    )
    schoolId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=True
    )

    amountPlanned: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="GNF", nullable=False)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)

    createdById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )

    region: Mapped["Region | None"] = relationship(lazy="raise")
    prefecture: Mapped["Prefecture | None"] = relationship(lazy="raise")
    subPrefecture: Mapped["SubPrefecture | None"] = relationship(lazy="raise")
    school: Mapped["School | None"] = relationship(lazy="raise")
    createdBy: Mapped["User | None"] = relationship(
        foreign_keys=[createdById], lazy="raise"
    )
    expenses: Mapped[list["Expense"]] = relationship(
        back_populates="budget", lazy="raise"
    )


class Expense(Base, TimestampMixin):
    __tablename__ = "Expense"
    __table_args__ = (
        Index("ix_Expense_budgetId", "budgetId"),
        Index("ix_Expense_schoolId_expenseDate", "schoolId", "expenseDate"),
        Index("ix_Expense_category_expenseDate", "category", "expenseDate"),
        Index("ix_Expense_status_expenseDate", "status", "expenseDate"),
    )

    id: Mapped[str] = cuid_pk()
    budgetId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Budget.id"), nullable=True
    )
    category: Mapped[BudgetCategory] = mapped_column(
        Enum(BudgetCategory, name="BudgetCategory", native_enum=True),
        nullable=False,
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="GNF", nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    expenseDate: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[ExpenseStatus] = mapped_column(
        Enum(ExpenseStatus, name="ExpenseStatus", native_enum=True),
        default=ExpenseStatus.PENDING,
        nullable=False,
    )

    # Bénéficiaire / scope (dupliqué pour la performance, vérifié à l'insert)
    schoolId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=True
    )
    regionId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Region.id"), nullable=True
    )
    prefectureId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Prefecture.id"), nullable=True
    )
    subPrefectureId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("SubPrefecture.id"), nullable=True
    )

    approvedById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )
    approvedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    createdById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )
    receiptUrl: Mapped[str | None] = mapped_column(String, nullable=True)

    budget: Mapped["Budget | None"] = relationship(
        back_populates="expenses", lazy="raise"
    )
    school: Mapped["School | None"] = relationship(lazy="raise")
    region: Mapped["Region | None"] = relationship(lazy="raise")
    prefecture: Mapped["Prefecture | None"] = relationship(lazy="raise")
    subPrefecture: Mapped["SubPrefecture | None"] = relationship(lazy="raise")
    approvedBy: Mapped["User | None"] = relationship(
        foreign_keys=[approvedById], lazy="raise"
    )
    createdBy: Mapped["User | None"] = relationship(
        foreign_keys=[createdById], lazy="raise"
    )


class PolicyUnitCost(Base, TimestampMixin):
    """Référentiel de coûts unitaires consommé par le simulateur de politique.

    Permet au ministère d'override les valeurs Banque Mondiale par défaut avec
    des coûts réels guinéens. Une seule ligne par `code`.
    """
    __tablename__ = "PolicyUnitCost"
    __table_args__ = (
        UniqueConstraint("code", name="uq_PolicyUnitCost_code"),
    )

    id: Mapped[str] = cuid_pk()
    code: Mapped[PolicyUnitCostCode] = mapped_column(
        Enum(PolicyUnitCostCode, name="PolicyUnitCostCode", native_enum=True),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    isActive: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updatedById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True
    )
    updatedBy: Mapped["User | None"] = relationship(
        foreign_keys=[updatedById], lazy="raise"
    )
