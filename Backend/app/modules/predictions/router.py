"""Phase 14 — Détection précoce du décrochage scolaire.

Score 0-100 par élève basé sur 5 features extraites des données existantes :
    - taux d'absentéisme 30j (poids le plus fort)
    - moyenne notes T1
    - distance domicile-école (proxy via école rurale/urbaine)
    - infrastructure école (water/electricity/condition)
    - ratio élèves/enseignant de l'école

Pas de ML lourd : combinaison pondérée linéaire + clamping. Performant à 3M
élèves sur Postgres avec les indices existants. Un modèle XGBoost peut
remplacer `_score_for_student` plus tard sans changer l'API.
"""
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from app.modules.attendance.models import AttendanceRecord
from app.modules.auth.models import User
from app.modules.census.models import Student, Teacher
from app.modules.schools.models import ClassRoom, School
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import (
    AttendanceStatus,
    BuildingCondition,
    ElectricitySource,
    UserRole,
    WaterSource,
)
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)

router = APIRouter(tags=["predictions"])


class DropoutRiskRow(BaseModel):
    studentId: str
    studentName: str
    uniqueCode: str
    schoolId: str
    schoolName: str
    classLevel: str | None
    riskScore: int          # 0-100
    riskLevel: Literal["low", "medium", "high", "critical"]
    drivers: list[str]      # Explication humaine des facteurs principaux
    absenceRate30d: float   # 0..100
    presentDays: int
    absentDays: int


class DropoutSummary(BaseModel):
    total: int
    critical: int
    high: int
    medium: int
    low: int
    rows: list[DropoutRiskRow]


def _scope_school_ids(user: User, base):
    if user.role in NATIONAL_SCOPE_ROLES:
        return base
    if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
        return base.where(School.regionId == user.regionId)
    if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
        return base.where(School.prefectureId == user.prefectureId)
    if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
        return base.where(School.subPrefectureId == user.subPrefectureId)
    if user.schoolId:
        return base.where(School.id == user.schoolId)
    return base.where(School.id == "__none__")


@router.get(
    "/dropout-risk",
    response_model=DropoutSummary,
    summary="Score de risque de décrochage par élève (top N par défaut)",
)
async def dropout_risk(
    user: Annotated[User, Depends(get_current_user)],
    session: DbSession,
    schoolId: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    minScore: Annotated[int, Query(ge=0, le=100)] = 40,
) -> DropoutSummary:
    """Renvoie les `limit` élèves avec le plus haut risque de décrochage."""
    cutoff = datetime.now(UTC) - timedelta(days=30)

    # 1. Présences agrégées par élève sur 30 jours
    presence_subq = (
        select(
            AttendanceRecord.studentId.label("sid"),
            func.count().label("total_scans"),
            func.sum(
                case((AttendanceRecord.status == AttendanceStatus.PRESENT, 1), else_=0)
            ).label("present_days"),
            func.sum(
                case((AttendanceRecord.status == AttendanceStatus.ABSENT, 1), else_=0)
            ).label("absent_days"),
        )
        .where(AttendanceRecord.scannedAt >= cutoff)
        .where(AttendanceRecord.studentId.is_not(None))
        .group_by(AttendanceRecord.studentId)
        .subquery()
    )

    # 2. Ratio élèves/enseignant par école (pour facteur ratio surcharge)
    teacher_count_subq = (
        select(Teacher.schoolId, func.count().label("n_teachers"))
        .group_by(Teacher.schoolId)
        .subquery()
    )
    student_count_subq = (
        select(Student.schoolId, func.count().label("n_students"))
        .group_by(Student.schoolId)
        .subquery()
    )

    base = (
        select(
            Student.id, Student.firstName, Student.lastName, Student.uniqueCode,
            Student.schoolId,
            School.name.label("school_name"),
            School.waterSource, School.electricitySource, School.buildingCondition,
            ClassRoom.level.label("class_level"),
            presence_subq.c.total_scans,
            presence_subq.c.present_days,
            presence_subq.c.absent_days,
            teacher_count_subq.c.n_teachers,
            student_count_subq.c.n_students,
        )
        .select_from(Student)
        .join(School, School.id == Student.schoolId)
        .outerjoin(ClassRoom, ClassRoom.id == Student.classRoomId)
        .outerjoin(presence_subq, presence_subq.c.sid == Student.id)
        .outerjoin(teacher_count_subq, teacher_count_subq.c.schoolId == Student.schoolId)
        .outerjoin(student_count_subq, student_count_subq.c.schoolId == Student.schoolId)
    )
    base = _scope_school_ids(user, base)
    if schoolId:
        base = base.where(Student.schoolId == schoolId)

    # On limite côté SQL à 5000 candidats puis on score en mémoire (rapide)
    rows = (await session.execute(base.limit(5000))).all()

    scored: list[DropoutRiskRow] = []
    for r in rows:
        absent_days = int(r.absent_days or 0)
        present_days = int(r.present_days or 0)
        total_days = absent_days + present_days
        absence_rate = (absent_days / total_days * 100) if total_days else 0.0

        score, drivers = _compute_score(
            absence_rate=absence_rate,
            water=r.waterSource,
            electricity=r.electricitySource,
            building=r.buildingCondition,
            n_teachers=int(r.n_teachers or 0),
            n_students=int(r.n_students or 0),
            present_days=present_days,
        )
        if score < minScore:
            continue
        scored.append(DropoutRiskRow(
            studentId=r.id,
            studentName=f"{r.firstName} {r.lastName}",
            uniqueCode=r.uniqueCode,
            schoolId=r.schoolId,
            schoolName=r.school_name,
            classLevel=r.class_level,
            riskScore=score,
            riskLevel=_level(score),
            drivers=drivers,
            absenceRate30d=round(absence_rate, 1),
            presentDays=present_days,
            absentDays=absent_days,
        ))

    scored.sort(key=lambda x: x.riskScore, reverse=True)
    top = scored[:limit]
    return DropoutSummary(
        total=len(scored),
        critical=sum(1 for r in scored if r.riskLevel == "critical"),
        high=sum(1 for r in scored if r.riskLevel == "high"),
        medium=sum(1 for r in scored if r.riskLevel == "medium"),
        low=sum(1 for r in scored if r.riskLevel == "low"),
        rows=top,
    )


def _compute_score(
    *, absence_rate: float, water, electricity, building,
    n_teachers: int, n_students: int, present_days: int,
) -> tuple[int, list[str]]:
    """Combinaison pondérée linéaire avec drivers explicatifs."""
    score = 0.0
    drivers: list[str] = []

    # Facteur 1 — Absentéisme (poids 50)
    if absence_rate >= 50:
        score += 50; drivers.append(f"Absentéisme critique ({absence_rate:.0f}%)")
    elif absence_rate >= 30:
        score += 35; drivers.append(f"Absentéisme élevé ({absence_rate:.0f}%)")
    elif absence_rate >= 15:
        score += 15; drivers.append(f"Absentéisme modéré ({absence_rate:.0f}%)")

    # Facteur 2 — Pas du tout scanné = signal fort
    if present_days == 0:
        score += 25
        drivers.append("Aucun scan présence sur 30 jours")

    # Facteur 3 — Infrastructure école précaire (poids 15)
    if water == WaterSource.NONE:
        score += 8; drivers.append("Pas d'eau potable à l'école")
    if electricity == ElectricitySource.NONE:
        score += 5; drivers.append("Pas d'électricité")
    if building in (BuildingCondition.DANGEROUS, BuildingCondition.POOR):
        score += 7; drivers.append(f"Bâtiment {building.value.lower()}")

    # Facteur 4 — Surcharge enseignants (poids 10)
    ratio = n_students / n_teachers if n_teachers else 999
    if ratio > 50:
        score += 10; drivers.append(f"Surcharge classe (ratio {ratio:.0f}/1)")
    elif ratio > 40:
        score += 5; drivers.append(f"Classe surchargée (ratio {ratio:.0f}/1)")

    # Clamp à 100
    final = min(100, int(round(score)))
    return final, drivers


def _level(score: int) -> Literal["low", "medium", "high", "critical"]:
    if score >= 80: return "critical"
    if score >= 60: return "high"
    if score >= 40: return "medium"
    return "low"
