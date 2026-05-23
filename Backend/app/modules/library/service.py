"""Library service — port of the NestJS LibraryService.

Same JSON shapes (mapInventory / mapLoan), same scope rules, same date format
(``DD/MM/YYYY`` à la française).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.academics.models import Subject
from app.modules.auth.models import User
from app.modules.census.models import Student
from app.modules.library.models import LibraryInventory, LibraryLoan
from app.modules.library.schemas import (
    InventoryPage,
    InventoryRow,
    LibraryInventoryQuery,
    LibraryLoansQuery,
    LoanRow,
    LoansPage,
)
from app.modules.schools.models import ClassRoom, School
from app.shared.enums import LibraryLoanStatus
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)


def _format_fr(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%d/%m/%Y")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


class LibraryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # INVENTORY
    # ==================================================================
    async def list_inventory(
        self, user: User, query: LibraryInventoryQuery
    ) -> InventoryPage:
        page = max(1, query.page)
        page_size = max(1, min(500, query.pageSize))

        base = select(LibraryInventory).options(
            selectinload(LibraryInventory.school).selectinload(School.region),
            selectinload(LibraryInventory.subject),
            selectinload(LibraryInventory.loans),
        )
        base = self._scope_inventory(base, user)

        if query.regionId:
            base = base.where(
                LibraryInventory.schoolId.in_(
                    select(School.id).where(School.regionId == query.regionId)
                )
            )
        if query.schoolId:
            base = base.where(LibraryInventory.schoolId == query.schoolId)
        if query.subjectId:
            base = base.where(LibraryInventory.subjectId == query.subjectId)
        if query.status:
            base = base.where(LibraryInventory.status == query.status)

        search = _clean(query.search)
        if search:
            pattern = f"%{search}%"
            base = base.where(
                or_(
                    LibraryInventory.title.ilike(pattern),
                    LibraryInventory.level.ilike(pattern),
                    LibraryInventory.schoolId.in_(
                        select(School.id).where(
                            or_(School.name.ilike(pattern), School.code.ilike(pattern))
                        )
                    ),
                    LibraryInventory.subjectId.in_(
                        select(Subject.id).where(Subject.name.ilike(pattern))
                    ),
                )
            )

        # Count BEFORE pagination
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()

        ordered = base.order_by(
            LibraryInventory.status.asc(),
            # School/subject sort handled in mem after the query (rel cols unavailable here)
            LibraryInventory.level.asc(),
        ).offset((page - 1) * page_size).limit(page_size)
        rows = (await self.session.execute(ordered)).scalars().unique().all()
        # Stable secondary sort by school name then subject name (NestJS contract)
        rows = sorted(
            rows,
            key=lambda r: (
                r.status.value,
                r.school.name if r.school else "",
                r.subject.name if r.subject else "",
                r.level or "",
            ),
        )
        return InventoryPage(
            rows=[self._map_inventory(r) for r in rows],
            total=total, page=page, pageSize=page_size,
        )

    # ==================================================================
    # LOANS
    # ==================================================================
    async def list_loans(self, user: User, query: LibraryLoansQuery) -> LoansPage:
        page = max(1, query.page)
        page_size = max(1, min(500, query.pageSize))

        base = select(LibraryLoan).options(
            selectinload(LibraryLoan.inventory).selectinload(LibraryInventory.school).selectinload(
                School.region
            ),
            selectinload(LibraryLoan.inventory).selectinload(LibraryInventory.subject),
            selectinload(LibraryLoan.student).selectinload(Student.school).selectinload(
                School.region
            ),
            selectinload(LibraryLoan.student).selectinload(Student.classRoom),
        )
        base = self._scope_loans(base, user)

        if query.schoolId:
            base = base.where(
                LibraryLoan.inventoryId.in_(
                    select(LibraryInventory.id).where(
                        LibraryInventory.schoolId == query.schoolId
                    )
                )
            )
        if query.regionId:
            base = base.where(
                LibraryLoan.inventoryId.in_(
                    select(LibraryInventory.id).where(
                        LibraryInventory.schoolId.in_(
                            select(School.id).where(School.regionId == query.regionId)
                        )
                    )
                )
            )
        if query.status:
            base = base.where(LibraryLoan.status == query.status)

        search = _clean(query.search)
        if search:
            pattern = f"%{search}%"
            base = base.where(
                or_(
                    LibraryLoan.inventoryId.in_(
                        select(LibraryInventory.id).where(
                            LibraryInventory.title.ilike(pattern)
                        )
                    ),
                    LibraryLoan.inventoryId.in_(
                        select(LibraryInventory.id).where(
                            LibraryInventory.subjectId.in_(
                                select(Subject.id).where(Subject.name.ilike(pattern))
                            )
                        )
                    ),
                    LibraryLoan.inventoryId.in_(
                        select(LibraryInventory.id).where(
                            LibraryInventory.schoolId.in_(
                                select(School.id).where(School.name.ilike(pattern))
                            )
                        )
                    ),
                    LibraryLoan.studentId.in_(
                        select(Student.id).where(
                            or_(
                                Student.uniqueCode.ilike(pattern),
                                Student.firstName.ilike(pattern),
                                Student.lastName.ilike(pattern),
                            )
                        )
                    ),
                    LibraryLoan.studentId.in_(
                        select(Student.id).where(
                            Student.classRoomId.in_(
                                select(ClassRoom.id).where(ClassRoom.name.ilike(pattern))
                            )
                        )
                    ),
                )
            )

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()

        ordered = base.order_by(
            LibraryLoan.status.asc(), LibraryLoan.dueAt.asc()
        ).offset((page - 1) * page_size).limit(page_size)
        rows = (await self.session.execute(ordered)).scalars().unique().all()
        return LoansPage(
            rows=[self._map_loan(r) for r in rows],
            total=total, page=page, pageSize=page_size,
        )

    # ==================================================================
    # PRIVATE HELPERS
    # ==================================================================
    def _scope_inventory(self, stmt: Any, user: User) -> Any:
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        scoped_school_ids = self._scoped_school_ids_subq(user)
        return stmt.where(LibraryInventory.schoolId.in_(scoped_school_ids))

    def _scope_loans(self, stmt: Any, user: User) -> Any:
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        scoped_school_ids = self._scoped_school_ids_subq(user)
        return stmt.where(
            LibraryLoan.inventoryId.in_(
                select(LibraryInventory.id).where(
                    LibraryInventory.schoolId.in_(scoped_school_ids)
                )
            )
        )

    @staticmethod
    def _scoped_school_ids_subq(user: User) -> Any:
        stmt = select(School.id)
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(School.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(School.prefectureId == user.prefectureId)
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(School.subPrefectureId == user.subPrefectureId)
        if user.schoolId:
            return stmt.where(School.id == user.schoolId)
        return stmt.where(School.id == "__none__")

    @staticmethod
    def _map_inventory(row: LibraryInventory) -> InventoryRow:
        loaned = sum(
            1
            for loan in (row.loans or [])
            if loan.status in (LibraryLoanStatus.BORROWED, LibraryLoanStatus.LATE)
        )
        coverage_rate = round((row.stock / row.required) * 100) if row.required else 0
        region_obj = row.school.region if row.school else None
        return InventoryRow(
            id=row.id,
            schoolId=row.schoolId,
            schoolName=row.school.name if row.school else "",
            code=row.school.code if row.school else "",
            regionId=row.school.regionId if row.school else None,
            region=region_obj.name if region_obj else "Région",
            level=row.level,
            subjectName=row.subject.name if row.subject else "",
            title=row.title,
            stock=row.stock,
            loaned=loaned,
            damaged=row.damaged,
            required=row.required,
            coverageRate=coverage_rate,
            status=row.status.value.lower(),  # type: ignore[arg-type]
            lastInventory=_format_fr(row.lastInventoryAt),
        )

    @staticmethod
    def _map_loan(loan: LibraryLoan) -> LoanRow:
        student = loan.student
        inv = loan.inventory
        class_name = (
            student.classRoom.name
            if student and student.classRoom
            else "Classe non affectée"
        )
        return LoanRow(
            id=loan.id,
            studentName=(
                f"{student.firstName} {student.lastName}" if student else ""
            ),
            uniqueCode=student.uniqueCode if student else "",
            schoolName=inv.school.name if inv and inv.school else "",
            className=class_name,
            title=inv.title if inv else "",
            borrowedAt=_format_fr(loan.borrowedAt),
            dueAt=_format_fr(loan.dueAt),
            status=loan.status.value.lower(),  # type: ignore[arg-type]
        )
