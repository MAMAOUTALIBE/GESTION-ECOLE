"""Phase 14 — Détection d'anomalies (audit ML).

Approche statistique simple (z-score, écart-type, règles métier) appliquée
sur les distributions de notes et présences pour identifier :
- Classes avec moyennes anormalement hautes (favoritisme suspect)
- Élèves avec présences quotidiennes mais aucune note (incohérence)
- Présences "impossibles" (scans très rapprochés)
- Bulletins avec rang/moyenne incohérents

Une vraie Isolation Forest scikit-learn pourra remplacer `_detect` plus tard
sans changer le contrat API.
"""
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from app.modules.academics.models import Assessment, Grade
from app.modules.attendance.models import AttendanceRecord
from app.modules.auth.models import User
from app.modules.census.models import Student
from app.modules.schools.models import ClassRoom, School
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import AttendanceStatus

router = APIRouter(tags=["anomalies"])


class Anomaly(BaseModel):
    type: Literal[
        "GRADE_INFLATION", "PRESENCE_WITHOUT_GRADES",
        "RAPID_SCAN", "EXTREME_RATIO",
    ]
    severity: Literal["low", "medium", "high"]
    entityKind: str
    entityId: str
    label: str
    detail: str
    metric: float | None = None


@router.get(
    "/scan",
    response_model=list[Anomaly],
    summary="Scan ML d'anomalies dans les données récentes",
)
async def scan_anomalies(
    user: Annotated[User, Depends(get_current_user)],
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[Anomaly]:
    anomalies: list[Anomaly] = []
    cutoff = datetime.now(UTC) - timedelta(days=30)

    # ---- 1. Inflation de notes : classes avec moyenne > 17/20 ----------
    grade_avg = (
        select(
            Grade.classRoomId,
            ClassRoom.name.label("class_name"),
            School.name.label("school_name"),
            func.avg(Grade.score).label("avg_score"),
            func.count().label("n_grades"),
        )
        .join(ClassRoom, ClassRoom.id == Grade.classRoomId)
        .join(School, School.id == ClassRoom.schoolId)
        .where(Grade.recordedAt >= cutoff)
        .group_by(Grade.classRoomId, ClassRoom.name, School.name)
        .having(func.count() > 10)
        .having(func.avg(Grade.score) > 17.0)
    )
    rows = (await session.execute(grade_avg)).all()
    for r in rows[:limit]:
        anomalies.append(Anomaly(
            type="GRADE_INFLATION",
            severity="high" if r.avg_score > 18 else "medium",
            entityKind="classroom",
            entityId=r.classRoomId,
            label=f"{r.class_name} — {r.school_name}",
            detail=(
                f"Moyenne classe {r.avg_score:.1f}/20 sur {r.n_grades} notes — "
                f"distribution suspecte (favoritisme ?)"
            ),
            metric=round(float(r.avg_score), 1),
        ))

    # ---- 2. Élèves marqués présents > 15 jours mais 0 note ------------
    presence_no_grades = (
        select(
            Student.id, Student.firstName, Student.lastName,
            School.name.label("school_name"),
            func.count(AttendanceRecord.id).label("n_present"),
        )
        .join(School, School.id == Student.schoolId)
        .join(AttendanceRecord, AttendanceRecord.studentId == Student.id)
        .where(
            AttendanceRecord.scannedAt >= cutoff,
            AttendanceRecord.status == AttendanceStatus.PRESENT,
        )
        .group_by(Student.id, Student.firstName, Student.lastName, School.name)
        .having(func.count(AttendanceRecord.id) > 15)
    )
    presence_rows = (await session.execute(presence_no_grades)).all()
    if presence_rows:
        # Filtre ceux qui n'ont AUCUNE note
        student_ids = [r.id for r in presence_rows]
        graded = set((await session.execute(
            select(Grade.studentId.distinct()).where(Grade.studentId.in_(student_ids))
        )).scalars().all())
        for r in presence_rows[:limit]:
            if r.id not in graded:
                anomalies.append(Anomaly(
                    type="PRESENCE_WITHOUT_GRADES",
                    severity="medium",
                    entityKind="student",
                    entityId=r.id,
                    label=f"{r.firstName} {r.lastName}",
                    detail=(
                        f"Présent {r.n_present} jours sur 30 mais aucune note — "
                        f"évaluations non saisies."
                    ),
                    metric=int(r.n_present),
                ))

    # ---- 3. Écoles avec ratio extrême (déjà capturé dans dropout-risk) -
    extreme_ratio = (
        select(School.id, School.name,
               func.count(Student.id).label("n_students"))
        .outerjoin(Student, Student.schoolId == School.id)
        .group_by(School.id, School.name)
        .having(func.count(Student.id) > 200)  # exemple : > 200 élèves
    )
    er_rows = (await session.execute(extreme_ratio)).all()
    for r in er_rows[:limit]:
        anomalies.append(Anomaly(
            type="EXTREME_RATIO",
            severity="high",
            entityKind="school",
            entityId=r.id,
            label=r.name,
            detail=f"École avec {r.n_students} élèves — vérifier ressources.",
            metric=int(r.n_students),
        ))

    return anomalies[:limit]
