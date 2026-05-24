"""Module 8 — Router predictions.

Deux familles d'endpoints :

1. Phase 14 (héritage) — ``GET /dropout-risk`` : heuristique pondérée
   linéaire calculée à la volée (pas de stockage). Conservé pour la
   compatibilité du dashboard Angular existant.

2. Module 8 — pipeline ML scikit-learn avec stockage :
   * ``POST /students/{student_id}/predict`` — calcule + persiste un score
   * ``POST /schools/{school_id}/batch-predict`` — async (202 + task_id)
   * ``GET  /schools/{school_id}/at-risk?level=HIGH``
   * ``POST /model/train`` (NATIONAL_ADMIN)
   * ``GET  /model/info``
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import case, func, select

from app.modules.attendance.models import AttendanceRecord
from app.modules.auth.models import User
from app.modules.census.models import Student, Teacher
from app.modules.predictions.enums import DropoutRiskLevel
from app.modules.predictions.schemas import (
    BatchPredictResponse,
    DropoutPredictionRead,
    ModelInfoResponse,
    TrainModelResponse,
)
from app.modules.predictions.service import PredictionService
from app.modules.predictions.training import train_initial_model_task
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
    require_roles,
)

router = APIRouter(tags=["predictions"])

# RBAC groups — niveau ≥ SCHOOL_DIRECTOR pour les opérations de scoring,
# NATIONAL_ADMIN uniquement pour l'entraînement / la gestion du modèle.
SCORING_ROLES = (
    UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN, UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN, UserRole.SCHOOL_DIRECTOR,
    UserRole.INSPECTOR,
)
TRAIN_ROLES = (UserRole.NATIONAL_ADMIN,)


# ===========================================================================
# Phase 14 héritage — heuristique pondérée pour le dashboard Angular
# ===========================================================================
class DropoutRiskRow(BaseModel):
    studentId: str
    studentName: str
    uniqueCode: str
    schoolId: str
    schoolName: str
    classLevel: str | None
    riskScore: int          # 0-100
    riskLevel: Literal["low", "medium", "high", "critical"]
    drivers: list[str]
    absenceRate30d: float
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
    summary="Score de risque (heuristique pondérée, Phase 14 — héritage)",
)
async def dropout_risk(
    user: Annotated[User, Depends(get_current_user)],
    session: DbSession,
    schoolId: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    minScore: Annotated[int, Query(ge=0, le=100)] = 40,
) -> DropoutSummary:
    cutoff = datetime.now(UTC) - timedelta(days=30)
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
    rows = (await session.execute(base.limit(5000))).all()

    scored: list[DropoutRiskRow] = []
    for r in rows:
        absent_days = int(r.absent_days or 0)
        present_days = int(r.present_days or 0)
        total_days = absent_days + present_days
        absence_rate = (absent_days / total_days * 100) if total_days else 0.0
        score, drivers = _compute_score(
            absence_rate=absence_rate, water=r.waterSource,
            electricity=r.electricitySource, building=r.buildingCondition,
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
    score = 0.0
    drivers: list[str] = []
    if absence_rate >= 50:
        score += 50
        drivers.append(f"Absentéisme critique ({absence_rate:.0f}%)")
    elif absence_rate >= 30:
        score += 35
        drivers.append(f"Absentéisme élevé ({absence_rate:.0f}%)")
    elif absence_rate >= 15:
        score += 15
        drivers.append(f"Absentéisme modéré ({absence_rate:.0f}%)")
    if present_days == 0:
        score += 25
        drivers.append("Aucun scan présence sur 30 jours")
    if water == WaterSource.NONE:
        score += 8
        drivers.append("Pas d'eau potable à l'école")
    if electricity == ElectricitySource.NONE:
        score += 5
        drivers.append("Pas d'électricité")
    if building in (BuildingCondition.DANGEROUS, BuildingCondition.POOR):
        score += 7
        drivers.append(f"Bâtiment {building.value.lower()}")
    ratio = n_students / n_teachers if n_teachers else 999
    if ratio > 50:
        score += 10
        drivers.append(f"Surcharge classe (ratio {ratio:.0f}/1)")
    elif ratio > 40:
        score += 5
        drivers.append(f"Classe surchargée (ratio {ratio:.0f}/1)")
    final = min(100, round(score))
    return final, drivers


def _level(score: int) -> Literal["low", "medium", "high", "critical"]:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


# ===========================================================================
# Module 8 — pipeline ML scikit-learn
# ===========================================================================
def _svc(session: DbSession) -> PredictionService:
    return PredictionService(session)


Svc = Annotated[PredictionService, Depends(_svc)]


@router.post(
    "/students/{student_id}/predict",
    response_model=DropoutPredictionRead,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*SCORING_ROLES))],
    summary="Calcule + persiste un score de dropout pour un élève",
)
async def predict_student(student_id: str, service: Svc) -> DropoutPredictionRead:
    pred = await service.predict_student(student_id)
    return DropoutPredictionRead.model_validate(pred)


@router.post(
    "/schools/{school_id}/batch-predict",
    response_model=BatchPredictResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*SCORING_ROLES))],
    summary="Calcule des scores pour tous les élèves d'une école (async)",
)
async def batch_predict_school(
    school_id: str, service: Svc,
) -> BatchPredictResponse:
    """MVP single-instance : on exécute en synchrone et on renvoie 202
    quand même pour rester compatible avec un futur Celery task asynchrone.
    """
    from app.shared.base import generate_cuid
    count = await service.batch_predict_school(school_id)
    return BatchPredictResponse(
        accepted=True,
        schoolId=school_id,
        taskId=f"sync-{generate_cuid()}",
        predicted=count,
    )


@router.get(
    "/schools/{school_id}/at-risk",
    response_model=list[DropoutPredictionRead],
    dependencies=[Depends(require_roles(*SCORING_ROLES))],
    summary="Liste les élèves à risque d'une école",
)
async def list_at_risk(
    school_id: str, service: Svc,
    level: Annotated[DropoutRiskLevel, Query()] = DropoutRiskLevel.HIGH,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[DropoutPredictionRead]:
    rows = await service.list_at_risk(school_id, level=level, limit=limit)
    return [DropoutPredictionRead.model_validate(r) for r in rows]


@router.post(
    "/model/train",
    response_model=TrainModelResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_roles(*TRAIN_ROLES))],
    summary="Entraîne un nouveau modèle (NATIONAL_ADMIN uniquement)",
)
async def train_model(session: DbSession) -> TrainModelResponse:
    from app.modules.predictions.service import _reset_model_cache
    version = await train_initial_model_task(session)
    _reset_model_cache()
    svc = PredictionService(session)
    meta = await svc.get_current_model_info()
    metrics = dict(meta.metrics) if meta and meta.metrics else {}
    return TrainModelResponse(version=version, metrics=metrics)


@router.get(
    "/model/info",
    response_model=ModelInfoResponse,
    dependencies=[Depends(require_roles(*SCORING_ROLES))],
    summary="Métadonnées du modèle courant",
)
async def model_info(session: DbSession) -> ModelInfoResponse:
    svc = PredictionService(session)
    meta = await svc.get_current_model_info()
    if meta is None:
        return ModelInfoResponse(
            version=None, trainedAt=None, metrics={},
            artifactPath=None, loaded=False,
        )
    import os as _os
    return ModelInfoResponse(
        version=meta.version,
        trainedAt=meta.trainedAt,
        metrics=dict(meta.metrics) if meta.metrics else {},
        artifactPath=meta.artifactPath,
        loaded=_os.path.exists(meta.artifactPath),
    )
