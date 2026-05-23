"""Pydantic schemas for the library module — mirror NestJS mapInventory()/mapLoan()."""
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.shared.enums import LibraryLoanStatus, LibraryStockStatus


# =============================================================
# QUERIES
# =============================================================
class LibraryInventoryQuery(BaseModel):
    """GET /api/library/inventory query params."""

    model_config = ConfigDict(str_strip_whitespace=True)

    search: str | None = None
    regionId: str | None = None
    schoolId: str | None = None
    subjectId: str | None = None
    status: LibraryStockStatus | None = None
    page: int = 1
    pageSize: int = 100


class LibraryLoansQuery(BaseModel):
    """GET /api/library/loans query params."""

    model_config = ConfigDict(str_strip_whitespace=True)

    search: str | None = None
    schoolId: str | None = None
    regionId: str | None = None
    status: LibraryLoanStatus | None = None
    page: int = 1
    pageSize: int = 100


# =============================================================
# RESPONSES — match NestJS mapInventory / mapLoan exactly
# =============================================================
class InventoryRow(BaseModel):
    id: str
    schoolId: str
    schoolName: str
    code: str
    regionId: str | None = None
    region: str
    level: str
    subjectName: str
    title: str
    stock: int
    loaned: int
    damaged: int
    required: int
    coverageRate: int
    status: Literal["sufficient", "watch", "shortage"]
    lastInventory: str  # fr-FR formatted date


class LoanRow(BaseModel):
    id: str
    studentName: str
    uniqueCode: str
    schoolName: str
    className: str
    title: str
    borrowedAt: str  # fr-FR formatted date
    dueAt: str
    status: Literal["borrowed", "late", "returned"]


class InventoryPage(BaseModel):
    rows: list[InventoryRow]
    total: int
    page: int
    pageSize: int


class LoansPage(BaseModel):
    rows: list[LoanRow]
    total: int
    page: int
    pageSize: int
