"""Analytics service — read-only KPIs aggregated across the whole platform.

All queries respect the territorial scope of the caller. Heavy-aggregation
endpoints (national KPIs, trends) run several COUNT queries in parallel via
``asyncio.gather`` to keep p99 latency reasonable on a 3M-student dataset.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Integer as _SAInteger
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession


def sa_int() -> _SAInteger:
    """Convenience: returns the SA Integer type used in CAST expressions."""
    return _SAInteger()


def _interpret_delta(metric: str, delta: float) -> str:
    """Petit message neutre pour les decisional deltas."""
    if abs(delta) < 0.01:
        return "Aucun changement notable."
    direction = "amélioration" if (
        # Plus d'élèves scolarisés / écoles / enseignants / classes = meilleure couverture
        (metric in ("students", "schools", "teachers", "classes") and delta > 0)
        # Ratios qui baissent = moins de pression sur l'enseignant/l'établissement
        or (metric in ("studentsPerTeacher", "studentsPerSchool") and delta < 0)
    ) else "dégradation"
    return f"{direction.capitalize()} de {abs(round(delta, 2))}"

from app.modules.academics.models import Parent, SchoolYear
from app.modules.analytics.schemas import (
    AttendancePoint,
    AttendanceTrends,
    AuditLogPage,
    AuditLogQuery,
    AuditLogRow,
    CohortLevelStats,
    CohortReport,
    EnrollmentPoint,
    EnrollmentTrends,
    EquityResponse,
    EquityRow,
    NationalKpis,
    PolicySimulationDelta,
    PolicySimulationRequest,
    PolicySimulationResponse,
    QualityResponse,
    TerritoriesResponse,
    TerritoryLevel,
    TerritoryRow,
    TopMetric,
    TopSchoolRow,
    TopSchoolsResponse,
)
from app.modules.attendance.models import AttendanceRecord
from app.modules.auth.models import User
from app.modules.census.models import Student, Teacher
from app.modules.finance.service import FinanceService
from app.modules.schools.models import ClassRoom, School, class_room_teacher_table
from app.modules.territory.models import Prefecture, Region, SubPrefecture
from app.modules.workflow.models import AuditLog
from app.shared.enums import (
    AttendanceStatus,
    ElectricitySource,
    Gender,
    PolicyUnitCostCode,
    ValidationStatus,
    WaterSource,
)
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)


def _round1(value: float) -> float:
    return round(value * 10) / 10


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # NATIONAL KPIs
    # ==================================================================
    async def national(self, user: User) -> NationalKpis:
        scoped_school_ids = self._scoped_school_ids_subq(user)

        async def _count(condition: Any, model: Any) -> int:
            stmt = select(func.count()).select_from(model).where(condition)
            return (await self.session.execute(stmt)).scalar_one()

        seven_days_ago = datetime.now(UTC) - timedelta(days=7)

        # Parallel KPI queries
        (
            students,
            teachers,
            schools,
            classes,
            geolocated,
            approved,
            pending,
            scans_total,
            scans_present,
            scans_late,
            scans_absent,
            parents_total,
            parents_reachable,
            regions,
        ) = await asyncio.gather(
            _count(Student.schoolId.in_(scoped_school_ids), Student),
            _count(Teacher.schoolId.in_(scoped_school_ids), Teacher),
            _count(School.id.in_(scoped_school_ids), School),
            _count(ClassRoom.schoolId.in_(scoped_school_ids), ClassRoom),
            _count(
                and_(
                    School.id.in_(scoped_school_ids),
                    School.latitude.is_not(None),
                    School.longitude.is_not(None),
                ),
                School,
            ),
            _count(
                and_(
                    School.id.in_(scoped_school_ids),
                    School.status == ValidationStatus.APPROVED,
                ),
                School,
            ),
            _count(
                and_(
                    School.id.in_(scoped_school_ids),
                    School.status == ValidationStatus.SUBMITTED,
                ),
                School,
            ),
            _count(
                and_(
                    AttendanceRecord.schoolId.in_(scoped_school_ids),
                    AttendanceRecord.scannedAt >= seven_days_ago,
                ),
                AttendanceRecord,
            ),
            _count(
                and_(
                    AttendanceRecord.schoolId.in_(scoped_school_ids),
                    AttendanceRecord.scannedAt >= seven_days_ago,
                    AttendanceRecord.status == AttendanceStatus.PRESENT,
                ),
                AttendanceRecord,
            ),
            _count(
                and_(
                    AttendanceRecord.schoolId.in_(scoped_school_ids),
                    AttendanceRecord.scannedAt >= seven_days_ago,
                    AttendanceRecord.status == AttendanceStatus.LATE,
                ),
                AttendanceRecord,
            ),
            _count(
                and_(
                    AttendanceRecord.schoolId.in_(scoped_school_ids),
                    AttendanceRecord.scannedAt >= seven_days_ago,
                    AttendanceRecord.status == AttendanceStatus.ABSENT,
                ),
                AttendanceRecord,
            ),
            _count(Parent.id.is_not(None), Parent),
            _count(
                or_(Parent.phone.is_not(None), Parent.email.is_not(None)),
                Parent,
            ),
            self._scoped_region_count(user),
        )

        return NationalKpis(
            students=students,
            teachers=teachers,
            schools=schools,
            classes=classes,
            regions=regions,
            studentsPerTeacher=_round1(students / teachers) if teachers else 0.0,
            studentsPerSchool=_round1(students / schools) if schools else 0.0,
            teachersPerSchool=_round1(teachers / schools) if schools else 0.0,
            geolocatedSchools=geolocated,
            gpsCoverageRate=round((geolocated / schools) * 100) if schools else 0,
            approvedSchools=approved,
            pendingSchools=pending,
            attendanceLast7Days=scans_total,
            presentLast7Days=scans_present,
            absentLast7Days=scans_absent,
            lateLast7Days=scans_late,
            presenceRateLast7Days=(
                _round1((scans_present / scans_total) * 100) if scans_total else 0.0
            ),
            parentReachable=parents_reachable,
            parentReachableRate=(
                _round1((parents_reachable / parents_total) * 100)
                if parents_total else 0.0
            ),
        )

    # ==================================================================
    # TERRITORIES — drill-down comparison
    # ==================================================================
    async def territories(
        self, user: User, level: TerritoryLevel
    ) -> TerritoriesResponse:
        scoped_school_ids = self._scoped_school_ids_subq(user)

        # Schools rows in scope (with parent ids only — names looked up in mem)
        schools_stmt = select(
            School.id,
            School.regionId,
            School.prefectureId,
            School.subPrefectureId,
            School.latitude,
            School.longitude,
        ).where(School.id.in_(scoped_school_ids))
        school_rows = (await self.session.execute(schools_stmt)).all()

        # Per-school counts
        sids = [r.id for r in school_rows]
        student_counts = await self._counts_by_school(Student, sids)
        teacher_counts = await self._counts_by_school(Teacher, sids)
        class_counts = await self._counts_by_school(ClassRoom, sids)

        # Group by territory
        groups: dict[str, dict[str, Any]] = {}
        for r in school_rows:
            if level == "region":
                key = r.regionId
            elif level == "prefecture":
                key = r.prefectureId or "__null__"
            else:
                key = r.subPrefectureId or "__null__"
            g = groups.setdefault(
                key,
                {
                    "id": key,
                    "schools": 0,
                    "students": 0,
                    "teachers": 0,
                    "classes": 0,
                    "geolocated": 0,
                    "regionId": r.regionId,
                },
            )
            g["schools"] += 1
            g["students"] += student_counts.get(r.id, 0)
            g["teachers"] += teacher_counts.get(r.id, 0)
            g["classes"] += class_counts.get(r.id, 0)
            if r.latitude is not None and r.longitude is not None:
                g["geolocated"] += 1

        # Resolve names in batch
        ids = [k for k in groups if k != "__null__"]
        names: dict[str, str] = {}
        parent_lookup: dict[str, tuple[str | None, str | None]] = {}
        if level == "region" and ids:
            for r in (
                await self.session.execute(
                    select(Region.id, Region.name).where(Region.id.in_(ids))
                )
            ).all():
                names[r.id] = r.name
        elif level == "prefecture" and ids:
            for r in (
                await self.session.execute(
                    select(Prefecture.id, Prefecture.name, Region.id, Region.name)
                    .join(Region, Region.id == Prefecture.regionId)
                    .where(Prefecture.id.in_(ids))
                )
            ).all():
                names[r[0]] = r[1]
                parent_lookup[r[0]] = (r[2], r[3])
        elif level == "sub-prefecture" and ids:
            for r in (
                await self.session.execute(
                    select(
                        SubPrefecture.id, SubPrefecture.name, Region.id, Region.name
                    )
                    .join(Region, Region.id == SubPrefecture.regionId)
                    .where(SubPrefecture.id.in_(ids))
                )
            ).all():
                names[r[0]] = r[1]
                parent_lookup[r[0]] = (r[2], r[3])

        rows: list[TerritoryRow] = []
        for key, g in groups.items():
            display_id = key if key != "__null__" else ""
            display_name = names.get(key, "Non renseigné" if key == "__null__" else "—")
            parent_id, parent_name = parent_lookup.get(key, (None, None))
            rows.append(
                TerritoryRow(
                    id=display_id,
                    name=display_name,
                    parentId=parent_id,
                    parentName=parent_name,
                    schools=g["schools"],
                    students=g["students"],
                    teachers=g["teachers"],
                    classes=g["classes"],
                    geolocatedSchools=g["geolocated"],
                    gpsCoverageRate=(
                        round((g["geolocated"] / g["schools"]) * 100)
                        if g["schools"] else 0
                    ),
                    studentsPerTeacher=(
                        _round1(g["students"] / g["teachers"])
                        if g["teachers"] else 0.0
                    ),
                    studentsPerSchool=(
                        _round1(g["students"] / g["schools"])
                        if g["schools"] else 0.0
                    ),
                )
            )
        rows.sort(key=lambda r: r.students, reverse=True)
        return TerritoriesResponse(level=level, total=len(rows), rows=rows)

    # ==================================================================
    # ATTENDANCE TRENDS
    # ==================================================================
    async def attendance_trends(
        self, user: User, days: int
    ) -> AttendanceTrends:
        scoped_school_ids = self._scoped_school_ids_subq(user)
        cutoff = (
            datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            - timedelta(days=days - 1)
        )

        day_col = func.date_trunc("day", AttendanceRecord.scannedAt).label("day")
        stmt = (
            select(
                day_col,
                AttendanceRecord.status,
                func.count().label("n"),
            )
            .where(
                AttendanceRecord.schoolId.in_(scoped_school_ids),
                AttendanceRecord.scannedAt >= cutoff,
            )
            .group_by(day_col, AttendanceRecord.status)
            .order_by(day_col.asc())
        )
        rows = (await self.session.execute(stmt)).all()

        # Aggregate by day → {present, late, absent, total}
        per_day: dict[date, dict[str, int]] = {}
        for row in rows:
            day_value = row.day.date() if isinstance(row.day, datetime) else row.day
            slot = per_day.setdefault(
                day_value, {"present": 0, "late": 0, "absent": 0, "total": 0}
            )
            slot["total"] += int(row.n)
            if row.status == AttendanceStatus.PRESENT:
                slot["present"] += int(row.n)
            elif row.status == AttendanceStatus.LATE:
                slot["late"] += int(row.n)
            elif row.status == AttendanceStatus.ABSENT:
                slot["absent"] += int(row.n)

        # Fill missing days with zeros so the UI gets a continuous line
        points: list[AttendancePoint] = []
        for offset in range(days):
            d = (cutoff + timedelta(days=offset)).date()
            slot = per_day.get(d, {"present": 0, "late": 0, "absent": 0, "total": 0})
            points.append(
                AttendancePoint(
                    day=d,
                    present=slot["present"],
                    late=slot["late"],
                    absent=slot["absent"],
                    total=slot["total"],
                    presenceRate=(
                        _round1((slot["present"] / slot["total"]) * 100)
                        if slot["total"] else 0.0
                    ),
                )
            )
        return AttendanceTrends(days=days, points=points)

    # ==================================================================
    # ENROLLMENT TRENDS — students + teachers created per month
    # ==================================================================
    async def enrollment_trends(
        self, user: User, months: int
    ) -> EnrollmentTrends:
        scoped_school_ids = self._scoped_school_ids_subq(user)
        cutoff = datetime.now(UTC).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ) - timedelta(days=months * 31)
        # The exact cutoff doesn't matter — we'll re-bucket by year/month below.

        month_expr_student = func.to_char(Student.createdAt, "YYYY-MM").label("month")
        month_expr_teacher = func.to_char(Teacher.createdAt, "YYYY-MM").label("month")

        students_stmt = (
            select(month_expr_student, func.count())
            .where(
                Student.schoolId.in_(scoped_school_ids),
                Student.createdAt >= cutoff,
            )
            .group_by(month_expr_student)
        )
        teachers_stmt = (
            select(month_expr_teacher, func.count())
            .where(
                Teacher.schoolId.in_(scoped_school_ids),
                Teacher.createdAt >= cutoff,
            )
            .group_by(month_expr_teacher)
        )

        students_rows, teachers_rows = await asyncio.gather(
            self.session.execute(students_stmt),
            self.session.execute(teachers_stmt),
        )
        students_by_month = dict(students_rows.all())
        teachers_by_month = dict(teachers_rows.all())

        # Generate a contiguous list of months (latest at the end)
        today = datetime.now(UTC).date().replace(day=1)
        labels: list[str] = []
        cursor = today
        for _ in range(months):
            labels.append(cursor.strftime("%Y-%m"))
            # step back one month
            if cursor.month == 1:
                cursor = cursor.replace(year=cursor.year - 1, month=12)
            else:
                cursor = cursor.replace(month=cursor.month - 1)
        labels.reverse()

        points = [
            EnrollmentPoint(
                month=label,
                students=int(students_by_month.get(label, 0)),
                teachers=int(teachers_by_month.get(label, 0)),
            )
            for label in labels
        ]
        return EnrollmentTrends(months=months, points=points)

    # ==================================================================
    # TOP SCHOOLS
    # ==================================================================
    async def top_schools(
        self, user: User, metric: TopMetric, limit: int
    ) -> TopSchoolsResponse:
        scoped_school_ids = self._scoped_school_ids_subq(user)

        # Load all candidate schools first (cap at limit*4 for `gps`/`ratio` which sort in mem)
        schools_stmt = select(
            School.id,
            School.code,
            School.name,
            School.regionId,
            Region.name.label("regionName"),
            School.latitude,
            School.longitude,
        ).join(Region, Region.id == School.regionId).where(
            School.id.in_(scoped_school_ids)
        )
        school_rows = (await self.session.execute(schools_stmt)).all()
        sids = [r.id for r in school_rows]
        students = await self._counts_by_school(Student, sids)
        teachers = await self._counts_by_school(Teacher, sids)
        classes = await self._counts_by_school(ClassRoom, sids)

        seven_days_ago = datetime.now(UTC) - timedelta(days=7)

        # Optional attendance counts (only when needed)
        attendance_present: dict[str, int] = {}
        attendance_total: dict[str, int] = {}
        if metric == "attendance":
            present_stmt = (
                select(AttendanceRecord.schoolId, func.count())
                .where(
                    AttendanceRecord.schoolId.in_(sids),
                    AttendanceRecord.scannedAt >= seven_days_ago,
                    AttendanceRecord.status == AttendanceStatus.PRESENT,
                )
                .group_by(AttendanceRecord.schoolId)
            )
            total_stmt = (
                select(AttendanceRecord.schoolId, func.count())
                .where(
                    AttendanceRecord.schoolId.in_(sids),
                    AttendanceRecord.scannedAt >= seven_days_ago,
                )
                .group_by(AttendanceRecord.schoolId)
            )
            present_rows, total_rows = await asyncio.gather(
                self.session.execute(present_stmt),
                self.session.execute(total_stmt),
            )
            attendance_present = {sid: int(n) for sid, n in present_rows.all()}
            attendance_total = {sid: int(n) for sid, n in total_rows.all()}

        rows = []
        for r in school_rows:
            geolocated = r.latitude is not None and r.longitude is not None
            presence_rate: float | None = None
            if metric == "attendance":
                t = attendance_total.get(r.id, 0)
                if t:
                    presence_rate = _round1(
                        (attendance_present.get(r.id, 0) / t) * 100
                    )
                else:
                    presence_rate = 0.0
            rows.append(
                TopSchoolRow(
                    id=r.id,
                    code=r.code,
                    name=r.name,
                    regionId=r.regionId,
                    regionName=r.regionName,
                    students=students.get(r.id, 0),
                    teachers=teachers.get(r.id, 0),
                    classes=classes.get(r.id, 0),
                    presenceRateLast7Days=presence_rate,
                    gpsCoverageRate=100 if geolocated else 0,
                )
            )

        if metric == "students":
            rows.sort(key=lambda x: x.students, reverse=True)
        elif metric == "attendance":
            rows.sort(
                key=lambda x: x.presenceRateLast7Days or 0.0, reverse=True
            )
        elif metric == "gps":
            rows.sort(key=lambda x: x.gpsCoverageRate or 0, reverse=True)
        elif metric == "ratio":
            rows.sort(
                key=lambda x: (
                    x.students / x.teachers if x.teachers else float("inf")
                )
            )

        return TopSchoolsResponse(metric=metric, limit=limit, rows=rows[:limit])

    # ==================================================================
    # QUALITY
    # ==================================================================
    async def quality(self, user: User) -> QualityResponse:
        scoped_school_ids = self._scoped_school_ids_subq(user)

        async def _count(condition: Any, model: Any) -> int:
            return (
                await self.session.execute(
                    select(func.count()).select_from(model).where(condition)
                )
            ).scalar_one()

        (
            students_total, students_no_class, students_no_photo, students_no_birth,
            teachers_total, teachers_no_classes, teachers_no_photo, teachers_no_birth,
            schools_total, schools_no_coords, schools_no_phone,
        ) = await asyncio.gather(
            _count(Student.schoolId.in_(scoped_school_ids), Student),
            _count(
                and_(
                    Student.schoolId.in_(scoped_school_ids),
                    Student.classRoomId.is_(None),
                ),
                Student,
            ),
            _count(
                and_(
                    Student.schoolId.in_(scoped_school_ids),
                    Student.photoUrl.is_(None),
                ),
                Student,
            ),
            _count(
                and_(
                    Student.schoolId.in_(scoped_school_ids),
                    Student.birthDate.is_(None),
                ),
                Student,
            ),
            _count(Teacher.schoolId.in_(scoped_school_ids), Teacher),
            _count(
                and_(
                    Teacher.schoolId.in_(scoped_school_ids),
                    ~Teacher.id.in_(select(class_room_teacher_table.c.B)),
                ),
                Teacher,
            ),
            _count(
                and_(
                    Teacher.schoolId.in_(scoped_school_ids),
                    Teacher.photoUrl.is_(None),
                ),
                Teacher,
            ),
            _count(
                and_(
                    Teacher.schoolId.in_(scoped_school_ids),
                    Teacher.birthDate.is_(None),
                ),
                Teacher,
            ),
            _count(School.id.in_(scoped_school_ids), School),
            _count(
                and_(
                    School.id.in_(scoped_school_ids),
                    or_(School.latitude.is_(None), School.longitude.is_(None)),
                ),
                School,
            ),
            _count(
                and_(
                    School.id.in_(scoped_school_ids), School.phone.is_(None)
                ),
                School,
            ),
        )

        missing = (
            students_no_class + students_no_photo + students_no_birth
            + teachers_no_classes + teachers_no_photo + teachers_no_birth
            + schools_no_coords + schools_no_phone
        )
        possible = students_total * 3 + teachers_total * 3 + schools_total * 2
        score = (
            max(0, round(((possible - missing) / possible) * 100))
            if possible else 100
        )
        return QualityResponse(
            score=score,
            studentsTotal=students_total,
            studentsWithoutClass=students_no_class,
            studentsWithoutPhoto=students_no_photo,
            studentsMissingBirthDate=students_no_birth,
            teachersTotal=teachers_total,
            teachersWithoutClasses=teachers_no_classes,
            teachersWithoutPhoto=teachers_no_photo,
            teachersMissingBirthDate=teachers_no_birth,
            schoolsTotal=schools_total,
            schoolsMissingCoordinates=schools_no_coords,
            schoolsMissingPhone=schools_no_phone,
        )

    # ==================================================================
    # SCOPE HELPERS
    # ==================================================================
    @staticmethod
    def _scoped_school_ids_subq(user: User) -> Any:
        stmt = select(School.id)
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(School.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(School.prefectureId == user.prefectureId)
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(School.subPrefectureId == user.subPrefectureId)
        if user.schoolId:
            return stmt.where(School.id == user.schoolId)
        return stmt.where(School.id == "__none__")

    async def _scoped_region_count(self, user: User) -> int:
        stmt = select(func.count()).select_from(Region)
        if user.role in NATIONAL_SCOPE_ROLES:
            pass
        elif user.regionId:
            stmt = stmt.where(Region.id == user.regionId)
        else:
            stmt = stmt.where(Region.id == "__none__")
        return (await self.session.execute(stmt)).scalar_one()

    # ==================================================================
    # FORECASTS — projection d'effectifs (régression linéaire simple)
    # ==================================================================
    async def enrollment_forecast(self, user: User, horizon_years: int) -> dict:
        """Régression linéaire sur les inscriptions mensuelles (24 derniers mois).

        Pour 3M élèves dans 12+ ans, ARIMA/Prophet seront à terme préférables ;
        en attendant ce modèle linéaire suffit pour donner une projection
        directionnelle utilisable côté planification ministérielle.
        """
        scoped_school_ids = self._scoped_school_ids_subq(user)
        cutoff = datetime.now(UTC) - timedelta(days=24 * 31)

        stmt = (
            select(
                func.to_char(Student.createdAt, "YYYY-MM").label("month"),
                func.count().label("n"),
            )
            .where(
                Student.schoolId.in_(scoped_school_ids),
                Student.createdAt >= cutoff,
            )
            .group_by("month")
            .order_by("month")
        )
        rows = (await self.session.execute(stmt)).all()
        # Effectif total actuel (utilisé dans tous les cas)
        total_current = (await self.session.execute(
            select(func.count()).select_from(Student).where(
                Student.schoolId.in_(scoped_school_ids)
            )
        )).scalar_one()

        if len(rows) < 3:
            # Fallback : pas assez d'historique → projection avec hypothèse
            # de croissance annuelle 4.5% (moyenne CONFEMEN Afrique de l'Ouest).
            from datetime import date as _date
            today = _date.today().replace(day=1)
            forecast = []
            cumulative = float(total_current)
            monthly_growth = (1 + 0.045) ** (1 / 12) - 1
            for k in range(1, horizon_years * 12 + 1):
                cumulative *= (1 + monthly_growth)
                future_month = today
                for _ in range(k):
                    future_month = (
                        future_month.replace(year=future_month.year + 1, month=1)
                        if future_month.month == 12
                        else future_month.replace(month=future_month.month + 1)
                    )
                forecast.append({
                    "month": future_month.strftime("%Y-%m"),
                    "newStudents": round(cumulative * monthly_growth, 1),
                    "cumulativeTotal": round(cumulative),
                })
            return {
                "horizonYears": horizon_years,
                "history": [{"month": "—", "students": total_current}],
                "forecast": forecast,
                "annualGrowthPct": 4.5,
                "totalCurrent": total_current,
                "totalForecasted": forecast[-1]["cumulativeTotal"] if forecast else total_current,
                "method": "growth-assumption-confemen-4.5pct",
            }

        # X = index mois (0..N-1), Y = élèves créés ce mois
        history = [{"month": r[0], "students": int(r[1])} for r in rows]
        x = list(range(len(history)))
        y = [h["students"] for h in history]
        n = len(x)
        # Régression linéaire fermée : slope, intercept
        x_mean = sum(x) / n
        y_mean = sum(y) / n
        denom = sum((xi - x_mean) ** 2 for xi in x) or 1
        slope = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y, strict=True)) / denom
        intercept = y_mean - slope * x_mean

        # Projection par mois sur l'horizon
        from datetime import date as _date
        today = _date.today().replace(day=1)
        forecast = []
        cumulative = total_current
        for k in range(1, horizon_years * 12 + 1):
            future_idx = n + k - 1
            predicted = max(0.0, slope * future_idx + intercept)
            cumulative += predicted
            future_month = today
            for _ in range(k):
                future_month = (
                    future_month.replace(year=future_month.year + 1, month=1)
                    if future_month.month == 12
                    else future_month.replace(month=future_month.month + 1)
                )
            forecast.append({
                "month": future_month.strftime("%Y-%m"),
                "newStudents": round(predicted, 1),
                "cumulativeTotal": round(cumulative),
            })

        # Croissance annuelle ≈ pente × 12 / moyenne mensuelle actuelle
        avg_monthly = y_mean if y_mean > 0 else 1.0
        annual_growth_pct = round((slope * 12 / avg_monthly) * 100, 1)

        return {
            "horizonYears": horizon_years,
            "history": history[-12:],  # garde 12 derniers mois pour le graphe
            "forecast": forecast,
            "annualGrowthPct": annual_growth_pct,
            "totalCurrent": total_current,
            "totalForecasted": forecast[-1]["cumulativeTotal"] if forecast else total_current,
            "method": "linear-regression",
        }

    async def _girls_toilets_coverage(self, user: User) -> int:
        scoped_school_ids = self._scoped_school_ids_subq(user)
        total = (await self.session.execute(
            select(func.count()).select_from(School).where(
                School.id.in_(scoped_school_ids)
            )
        )).scalar_one()
        if not total:
            return 0
        equipped = (await self.session.execute(
            select(func.count()).select_from(School).where(
                School.id.in_(scoped_school_ids),
                School.toiletsGirls.isnot(None),
                School.toiletsGirls > 0,
            )
        )).scalar_one()
        return round((equipped / total) * 100)

    async def _electricity_coverage(self, user: User) -> int:
        scoped_school_ids = self._scoped_school_ids_subq(user)
        total = (await self.session.execute(
            select(func.count()).select_from(School).where(
                School.id.in_(scoped_school_ids)
            )
        )).scalar_one()
        if not total:
            return 0
        equipped = (await self.session.execute(
            select(func.count()).select_from(School).where(
                School.id.in_(scoped_school_ids),
                School.electricitySource.isnot(None),
                School.electricitySource != ElectricitySource.NONE,
            )
        )).scalar_one()
        return round((equipped / total) * 100)

    async def _counts_by_school(
        self, model: Any, school_ids: list[str]
    ) -> dict[str, int]:
        if not school_ids:
            return {}
        rows = (
            await self.session.execute(
                select(model.schoolId, func.count())
                .where(model.schoolId.in_(school_ids))
                .group_by(model.schoolId)
            )
        ).all()
        return {sid: int(n) for sid, n in rows}

    # ==================================================================
    # COHORT ANALYSIS — niveau par niveau (CP1, CP2, …, CM2)
    # ==================================================================
    async def cohorts(
        self, user: User, school_year_id: str | None
    ) -> CohortReport:
        """Décompose les effectifs par level (= ClassRoom.level) avec genre + redoublants.

        Si ``school_year_id`` est fourni, on ne compte que les élèves dont
        la classe est rattachée à cette année scolaire (via ClassRoom.schoolYearId).
        """
        scoped_school_ids = self._scoped_school_ids_subq(user)

        # Le level vit sur ClassRoom — on JOINT Student avec ClassRoom
        stmt = (
            select(
                ClassRoom.level,
                Student.gender,
                func.count(),
            )
            .join(ClassRoom, ClassRoom.id == Student.classRoomId)
            .where(Student.schoolId.in_(scoped_school_ids))
            .where(ClassRoom.level.isnot(None))
            .group_by(ClassRoom.level, Student.gender)
        )
        if school_year_id:
            stmt = stmt.where(ClassRoom.schoolYearId == school_year_id)

        rows = (await self.session.execute(stmt)).all()

        # Récupération du nom de l'année scolaire pour le payload
        sy_name: str | None = None
        if school_year_id:
            sy = (await self.session.execute(
                select(SchoolYear).where(SchoolYear.id == school_year_id)
            )).scalar_one_or_none()
            sy_name = sy.name if sy else None

        # Aggrège par level
        per_level: dict[str, dict[str, int]] = {}
        for level, gender, count in rows:
            slot = per_level.setdefault(level, {"male": 0, "female": 0, "other": 0})
            if gender == Gender.MALE:
                slot["male"] += int(count)
            elif gender == Gender.FEMALE:
                slot["female"] += int(count)
            else:
                slot["other"] += int(count)

        # Compte des redoublants par level (heuristique: âge > âge attendu pour le niveau)
        # Niveaux primaires guinéens approximatifs : CP1 = 6 ans … CM2 = 11 ans
        expected_age_for_level = {
            "CP1": 6, "CP2": 7, "CE1": 8, "CE2": 9, "CM1": 10, "CM2": 11,
            "6e": 12, "5e": 13, "4e": 14, "3e": 15,
            "2nde": 16, "1ere": 17, "Tle": 18,
        }

        from datetime import date as _date
        today = _date.today()

        repeaters_per_level: dict[str, int] = {}
        avg_age_per_level: dict[str, float | None] = {}
        for level in per_level:
            expected = expected_age_for_level.get(level)
            ages_stmt = (
                select(Student.birthDate)
                .join(ClassRoom, ClassRoom.id == Student.classRoomId)
                .where(
                    Student.schoolId.in_(scoped_school_ids),
                    ClassRoom.level == level,
                    Student.birthDate.isnot(None),
                )
            )
            if school_year_id:
                ages_stmt = ages_stmt.where(ClassRoom.schoolYearId == school_year_id)
            ages_rows = (await self.session.execute(ages_stmt)).scalars().all()
            ages = [(today.year - bd.year) for bd in ages_rows if bd is not None]
            avg_age_per_level[level] = (
                round(sum(ages) / len(ages), 1) if ages else None
            )
            if expected and ages:
                # un redoublant = âge > âge attendu + 1
                repeaters_per_level[level] = sum(1 for a in ages if a > expected + 1)
            else:
                repeaters_per_level[level] = 0

        # Tri canonique des niveaux (CP1 < CP2 < CE1 < ... < Tle)
        canonical_order = list(expected_age_for_level.keys())
        sorted_levels = sorted(
            per_level.keys(),
            key=lambda lvl: canonical_order.index(lvl) if lvl in canonical_order else 999,
        )

        levels_payload = [
            CohortLevelStats(
                level=lvl,
                enrolled=sum(per_level[lvl].values()),
                male=per_level[lvl]["male"],
                female=per_level[lvl]["female"],
                repeaters=repeaters_per_level[lvl],
                averageAge=avg_age_per_level[lvl],
            )
            for lvl in sorted_levels
        ]
        total = sum(level.enrolled for level in levels_payload)
        total_repeaters = sum(level.repeaters for level in levels_payload)
        return CohortReport(
            schoolYearId=school_year_id,
            schoolYearName=sy_name,
            levels=levels_payload,
            totalStudents=total,
            totalRepeaters=total_repeaters,
            repeaterRate=round((total_repeaters / total) * 100, 1) if total else 0.0,
        )

    # ==================================================================
    # EQUITY INDEX — GPI (Gender Parity Index) + couvertures infra
    # ==================================================================
    async def equity(self, user: User) -> EquityResponse:
        scoped_school_ids = self._scoped_school_ids_subq(user)

        # Agrégation genre par région
        gender_stmt = (
            select(School.regionId, Student.gender, func.count())
            .join(School, School.id == Student.schoolId)
            .where(Student.schoolId.in_(scoped_school_ids))
            .group_by(School.regionId, Student.gender)
        )
        gender_rows = (await self.session.execute(gender_stmt)).all()

        # Agrégation infra par région
        infra_stmt = select(
            School.regionId,
            func.count().label("schools"),
            func.sum(
                func.cast(
                    (School.toiletsGirls.isnot(None)) & (School.toiletsGirls > 0),
                    sa_int(),
                )
            ).label("girls_toilets"),
            func.sum(
                func.cast(
                    (School.electricitySource.isnot(None))
                    & (School.electricitySource != ElectricitySource.NONE),
                    sa_int(),
                )
            ).label("electricity"),
            func.sum(
                func.cast(
                    (School.waterSource.isnot(None))
                    & (School.waterSource != WaterSource.NONE),
                    sa_int(),
                )
            ).label("water"),
        ).where(School.id.in_(scoped_school_ids)).group_by(School.regionId)
        infra_rows = (await self.session.execute(infra_stmt)).all()

        # Lookup nom région
        region_ids = {r.regionId for r in infra_rows if r.regionId}
        names: dict[str, str] = {}
        if region_ids:
            for r in (await self.session.execute(
                select(Region.id, Region.name).where(Region.id.in_(region_ids))
            )).all():
                names[r.id] = r.name

        # Agrégation par region
        per_region: dict[str, dict[str, int]] = {}
        for region_id, gender, count in gender_rows:
            slot = per_region.setdefault(
                region_id, {"male": 0, "female": 0, "students": 0}
            )
            if gender == Gender.MALE:
                slot["male"] += int(count)
            elif gender == Gender.FEMALE:
                slot["female"] += int(count)
            slot["students"] += int(count)

        for r in infra_rows:
            slot = per_region.setdefault(
                r.regionId, {"male": 0, "female": 0, "students": 0}
            )
            slot["schools"] = int(r.schools or 0)
            slot["girls_toilets"] = int(r.girls_toilets or 0)
            slot["electricity"] = int(r.electricity or 0)
            slot["water"] = int(r.water or 0)

        rows: list[EquityRow] = []
        for region_id, slot in per_region.items():
            schools = slot.get("schools", 0)
            male = slot.get("male", 0)
            female = slot.get("female", 0)
            gpi = round((female / male), 2) if male else 0.0
            rows.append(EquityRow(
                territoryId=region_id,
                territoryName=names.get(region_id, "—"),
                students=slot.get("students", 0),
                male=male,
                female=female,
                genderParityIndex=gpi,
                schoolsTotal=schools,
                schoolsWithGirlsToilets=slot.get("girls_toilets", 0),
                girlsToiletsCoverage=(
                    round((slot.get("girls_toilets", 0) / schools) * 100)
                    if schools else 0
                ),
                schoolsWithElectricity=slot.get("electricity", 0),
                electricityCoverage=(
                    round((slot.get("electricity", 0) / schools) * 100)
                    if schools else 0
                ),
                schoolsWithWater=slot.get("water", 0),
                waterCoverage=(
                    round((slot.get("water", 0) / schools) * 100)
                    if schools else 0
                ),
            ))
        rows.sort(key=lambda r: r.students, reverse=True)

        # Indicateurs nationaux (somme pondérée)
        total_male = sum(r.male for r in rows)
        total_female = sum(r.female for r in rows)
        total_schools = sum(r.schoolsTotal for r in rows)
        national_gpi = round((total_female / total_male), 2) if total_male else 0.0
        national_girls_toilets = sum(r.schoolsWithGirlsToilets for r in rows)
        national_electricity = sum(r.schoolsWithElectricity for r in rows)
        national_water = sum(r.schoolsWithWater for r in rows)

        return EquityResponse(
            rows=rows,
            nationalGpi=national_gpi,
            nationalGirlsToiletsCoverage=(
                round((national_girls_toilets / total_schools) * 100)
                if total_schools else 0
            ),
            nationalElectricityCoverage=(
                round((national_electricity / total_schools) * 100)
                if total_schools else 0
            ),
            nationalWaterCoverage=(
                round((national_water / total_schools) * 100)
                if total_schools else 0
            ),
        )

    # ==================================================================
    # POLICY SIMULATOR — what-if à horizon N années
    # ==================================================================
    async def policy_simulate(
        self, user: User, dto: PolicySimulationRequest
    ) -> PolicySimulationResponse:
        # Baseline : on ré-utilise le scope du caller, optionnellement filtré par région
        scope_user = user
        if dto.regionId:
            # Force le scope sur la région demandée si l'user est national
            class _ScopedUser:
                def __init__(self, base: User, region_id: str) -> None:
                    self.role = base.role
                    self.id = base.id
                    self.regionId = region_id
                    self.prefectureId = None
                    self.subPrefectureId = None
                    self.schoolId = None
            scope_user = _ScopedUser(user, dto.regionId)  # type: ignore[assignment]

        baseline_kpis = await self.national(scope_user)
        baseline = {
            "students": float(baseline_kpis.students),
            "teachers": float(baseline_kpis.teachers),
            "schools": float(baseline_kpis.schools),
            "classes": float(baseline_kpis.classes),
            "studentsPerTeacher": baseline_kpis.studentsPerTeacher,
            "studentsPerSchool": baseline_kpis.studentsPerSchool,
            "gpsCoverageRate": float(baseline_kpis.gpsCoverageRate),
        }

        # Hypothèses simples pour le scénario (assumées explicites dans la
        # réponse via `notes` pour ne pas tromper le décideur).
        students_per_new_school = (
            baseline_kpis.studentsPerSchool if baseline_kpis.studentsPerSchool > 0 else 250.0
        )
        new_students_covered = int(dto.addSchools * students_per_new_school)

        scenario_students = baseline["students"] + new_students_covered
        scenario_teachers = baseline["teachers"] + dto.addTeachers
        scenario_schools = baseline["schools"] + dto.addSchools
        scenario_classes = baseline["classes"] + dto.addClassrooms

        scenario = {
            "students": scenario_students,
            "teachers": scenario_teachers,
            "schools": scenario_schools,
            "classes": scenario_classes,
            "studentsPerTeacher": (
                round((scenario_students / scenario_teachers) * 10) / 10
                if scenario_teachers else 0.0
            ),
            "studentsPerSchool": (
                round((scenario_students / scenario_schools) * 10) / 10
                if scenario_schools else 0.0
            ),
            "gpsCoverageRate": baseline["gpsCoverageRate"],  # inchangé
        }

        deltas: list[PolicySimulationDelta] = []
        for metric, label in (
            ("students", "Effectif total élèves"),
            ("teachers", "Effectif total enseignants"),
            ("schools", "Nombre d'écoles"),
            ("studentsPerTeacher", "Ratio élèves / enseignant"),
            ("studentsPerSchool", "Ratio élèves / école"),
        ):
            b = baseline[metric]
            s = scenario[metric]
            d = s - b
            interpretation = _interpret_delta(metric, d)
            deltas.append(PolicySimulationDelta(
                metric=label,
                baseline=b,
                scenario=s,
                delta=round(d, 1),
                deltaPct=round((d / b) * 100, 1) if b else None,
                interpretation=interpretation,
            ))

        # Coûts unitaires : on charge le référentiel Phase 11 (Finance) et
        # on retombe sur les valeurs Banque Mondiale 2023 si une ligne manque.
        finance = FinanceService(self.session)
        unit_costs = await finance.get_unit_costs_map()
        BM_DEFAULTS = {
            PolicyUnitCostCode.NEW_SCHOOL: 150_000.0,
            PolicyUnitCostCode.NEW_CLASSROOM: 25_000.0,
            PolicyUnitCostCode.TEACHER_YEAR: 5_000.0,
            PolicyUnitCostCode.GIRLS_TOILETS: 5_000.0,
            PolicyUnitCostCode.ELECTRICITY_SOLAR: 8_000.0,
            PolicyUnitCostCode.WATER_BOREHOLE: 10_000.0,
        }
        def _cost(code: PolicyUnitCostCode) -> tuple[float, bool]:
            """Retourne (montant, override) — override=True si Finance a une valeur."""
            if code in unit_costs:
                return unit_costs[code], True
            return BM_DEFAULTS[code], False

        cost_new_school, school_overridden = _cost(PolicyUnitCostCode.NEW_SCHOOL)
        cost_new_classroom, classroom_overridden = _cost(PolicyUnitCostCode.NEW_CLASSROOM)
        cost_teacher_year, teacher_overridden = _cost(PolicyUnitCostCode.TEACHER_YEAR)
        cost_girls_toilets, _ = _cost(PolicyUnitCostCode.GIRLS_TOILETS)
        cost_elec_solar, _ = _cost(PolicyUnitCostCode.ELECTRICITY_SOLAR)

        cost_schools = dto.addSchools * cost_new_school
        cost_classrooms = dto.addClassrooms * cost_new_classroom
        cost_teachers_horizon = dto.addTeachers * cost_teacher_year * dto.horizonYears

        cost_targets = 0.0
        target_notes: list[str] = []

        # Couverture actuelle réelle (pas la couverture GPS — bug fixé)
        girls_coverage_now = await self._girls_toilets_coverage(scope_user)
        electricity_coverage_now = await self._electricity_coverage(scope_user)

        if dto.targetGirlsToiletsCoverage is not None:
            schools_to_equip = int(max(
                0,
                (dto.targetGirlsToiletsCoverage - girls_coverage_now) / 100
                * baseline["schools"],
            ))
            cost_targets += schools_to_equip * cost_girls_toilets
            target_notes.append(
                f"Toilettes filles : couverture actuelle {girls_coverage_now}% "
                f"→ cible {dto.targetGirlsToiletsCoverage}% = {schools_to_equip} "
                f"écoles à équiper (~{int(cost_girls_toilets)}$/école)"
            )
        if dto.targetElectricityCoverage is not None:
            schools_to_equip = int(max(
                0,
                (dto.targetElectricityCoverage - electricity_coverage_now) / 100
                * baseline["schools"],
            ))
            cost_targets += schools_to_equip * cost_elec_solar
            target_notes.append(
                f"Électricité solaire : couverture actuelle {electricity_coverage_now}% "
                f"→ cible {dto.targetElectricityCoverage}% = {schools_to_equip} "
                f"écoles à équiper (~{int(cost_elec_solar)}$/école)"
            )

        total_cost = (
            cost_schools + cost_classrooms + cost_teachers_horizon + cost_targets
        )

        cost_source = (
            "Référentiel Finance (Phase 11) — overrides ministère"
            if (school_overridden or classroom_overridden or teacher_overridden)
            else "Banque Mondiale Afrique de l'Ouest 2023 (défauts)"
        )
        notes = [
            "Hypothèses : 250 élèves/nouvelle école si baseline manquante.",
            f"Source coûts unitaires : {cost_source}.",
            f"  • Nouvelle école primaire : {int(cost_new_school):,} USD",
            f"  • Nouvelle salle de classe : {int(cost_new_classroom):,} USD",
            f"  • Enseignant fonctionnaire/an : {int(cost_teacher_year):,} USD",
            f"Salaire enseignants extrapolé sur {dto.horizonYears} ans.",
            *target_notes,
        ]

        return PolicySimulationResponse(
            regionId=dto.regionId,
            horizonYears=dto.horizonYears,
            baseline=baseline,
            scenario=scenario,
            deltas=deltas,
            estimatedAdditionalStudentsCovered=new_students_covered,
            estimatedCostUSD=float(total_cost),
            notes=notes,
        )

    # ==================================================================
    # AUDIT LOG (admin-only, paginated)
    # ==================================================================
    async def list_audit_logs(self, query: AuditLogQuery) -> AuditLogPage:
        page = max(1, query.page)
        page_size = max(1, min(500, query.pageSize))

        base = select(AuditLog)
        if query.actorId:
            base = base.where(AuditLog.actorId == query.actorId)
        if query.entity:
            base = base.where(AuditLog.entity == query.entity)
        if query.entityId:
            base = base.where(AuditLog.entityId == query.entityId)
        if query.action:
            base = base.where(AuditLog.action == query.action)

        total = (
            await self.session.execute(
                select(func.count()).select_from(base.subquery())
            )
        ).scalar_one()

        ordered = (
            base.order_by(AuditLog.createdAt.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.execute(ordered)).scalars().all()
        return AuditLogPage(
            rows=[AuditLogRow.model_validate(r) for r in rows],
            total=total, page=page, pageSize=page_size,
        )
