"""Module 9 — Rule-based detectors.

Chaque détecteur retourne une liste de ``AnomalyDetection`` non persistées
(le ``AnomalyService`` les sauvegarde en bloc). Tous prennent en option un
``school_id`` pour scoper la détection à une école (sinon : balayage global).

Conventions
-----------
* Requêtes SQL minimales — pas de joins lourds. ``LIMIT`` systématique pour
  éviter d'écrouler la DB sur un dataset corrompu (un seuil de 1000
  anomalies par détecteur est largement suffisant en pratique).
* On ne déduplique PAS ici : le service de listing montre la dernière
  occurrence par ``(entityType, entityId, type)``. La duplication temporelle
  est volontaire — elle permet de mesurer la persistance d'un signal.
* Chaque détecteur peuple ``evidence`` avec les champs exacts qui ont
  déclenché la règle (ex. score brut, dates, IDs source) — c'est lu par le
  directeur d'école pour comprendre la décision.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import AcademicPeriod, Grade, ReportCard
from app.modules.anomalies.enums import AnomalySeverity, AnomalyType
from app.modules.anomalies.models import AnomalyDetection
from app.modules.attendance.models import AttendanceRecord
from app.modules.census.models import Student, StudentTransfer
from app.modules.schools.models import School
from app.shared.base import generate_cuid
from app.shared.enums import AttendanceStatus

# Limite défensive — au-delà, on suppose qu'il y a un problème systémique
# et qu'il faut nettoyer la donnée source plutôt que générer des milliers
# d'anomalies.
PER_DETECTOR_LIMIT = 1000


def _make(
    *,
    a_type: AnomalyType,
    severity: AnomalySeverity,
    entity_type: str,
    entity_id: str,
    description: str,
    evidence: dict[str, Any],
    school_id: str | None = None,
    region_id: str | None = None,
    detected_at: datetime | None = None,
) -> AnomalyDetection:
    """Construit une AnomalyDetection non persistée."""
    return AnomalyDetection(
        id=generate_cuid(),
        type=a_type,
        severity=severity,
        entityType=entity_type,
        entityId=entity_id,
        description=description,
        evidence=evidence,
        schoolId=school_id,
        regionId=region_id,
        detectedAt=detected_at or datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# 1. Notes impossibles (score < 0 ou > 20)
# ---------------------------------------------------------------------------
async def detect_impossible_grades(
    session: AsyncSession, school_id: str | None = None,
) -> list[AnomalyDetection]:
    """Notes hors de l'intervalle [0, 20].

    Severity CRITICAL parce qu'il s'agit d'une violation d'invariant métier
    et que le bulletin peut être faux.
    """
    stmt = (
        select(
            Grade.id, Grade.studentId, Grade.score, Grade.assessmentId,
            Student.schoolId, School.regionId,
        )
        .join(Student, Student.id == Grade.studentId)
        .join(School, School.id == Student.schoolId)
        .where(or_(Grade.score < 0.0, Grade.score > 20.0))
        .limit(PER_DETECTOR_LIMIT)
    )
    if school_id is not None:
        stmt = stmt.where(Student.schoolId == school_id)

    rows = (await session.execute(stmt)).all()
    out: list[AnomalyDetection] = []
    for r in rows:
        out.append(_make(
            a_type=AnomalyType.IMPOSSIBLE_GRADE,
            severity=AnomalySeverity.CRITICAL,
            entity_type="Grade",
            entity_id=r.id,
            description=(
                f"Note impossible : {r.score:.2f}/20 "
                f"(devrait être dans [0, 20])."
            ),
            evidence={
                "gradeId": r.id,
                "studentId": r.studentId,
                "assessmentId": r.assessmentId,
                "score": float(r.score),
                "expectedMin": 0.0,
                "expectedMax": 20.0,
            },
            school_id=r.schoolId,
            region_id=r.regionId,
        ))
    return out


# ---------------------------------------------------------------------------
# 2. Présence 100% suspecte (sur >= 60 jours consécutifs distincts)
# ---------------------------------------------------------------------------
async def detect_suspicious_attendance_100(
    session: AsyncSession,
    school_id: str | None = None,
    min_days: int = 60,
) -> list[AnomalyDetection]:
    """Élèves avec 100% de présence sur ≥ ``min_days`` jours distincts.

    Aucun élève réel n'a jamais 100% sur 60+ jours (maladie, événement
    familial…). Soit la saisie est mécanique (le directeur clique
    "PRESENT" pour tout le monde), soit l'élève a quitté l'école sans
    être déscolarisé. Severity MEDIUM (à investiguer mais pas un
    blocage).
    """
    days_present = func.count(
        func.distinct(func.date(AttendanceRecord.scannedAt))
    )
    days_absent = func.sum(
        case(
            (AttendanceRecord.status == AttendanceStatus.ABSENT, 1),
            else_=0,
        )
    )
    stmt = (
        select(
            AttendanceRecord.studentId,
            Student.schoolId,
            School.regionId,
            days_present.label("days_present"),
            days_absent.label("days_absent"),
        )
        .join(Student, Student.id == AttendanceRecord.studentId)
        .join(School, School.id == Student.schoolId)
        .where(
            AttendanceRecord.studentId.is_not(None),
            AttendanceRecord.status == AttendanceStatus.PRESENT,
        )
        .group_by(
            AttendanceRecord.studentId, Student.schoolId, School.regionId,
        )
        .having(days_present >= min_days)
        .limit(PER_DETECTOR_LIMIT)
    )
    if school_id is not None:
        stmt = stmt.where(Student.schoolId == school_id)

    rows = (await session.execute(stmt)).all()
    out: list[AnomalyDetection] = []
    for r in rows:
        # On ne reporte que si jamais d'absence sur cette période.
        if int(r.days_absent or 0) > 0:
            continue
        out.append(_make(
            a_type=AnomalyType.SUSPICIOUS_ATTENDANCE,
            severity=AnomalySeverity.MEDIUM,
            entity_type="Student",
            entity_id=r.studentId,
            description=(
                f"Présence 100% sur {r.days_present} jours consécutifs — "
                "saisie potentiellement automatique ou élève absent du suivi."
            ),
            evidence={
                "studentId": r.studentId,
                "daysPresent": int(r.days_present),
                "daysAbsent": 0,
                "thresholdDays": min_days,
            },
            school_id=r.schoolId,
            region_id=r.regionId,
        ))
    return out


# ---------------------------------------------------------------------------
# 3. Saut de moyenne (delta > 8 points entre deux périodes successives)
# ---------------------------------------------------------------------------
async def detect_grade_jump(
    session: AsyncSession,
    school_id: str | None = None,
    delta_threshold: float = 8.0,
) -> list[AnomalyDetection]:
    """Élèves avec une moyenne qui change brutalement entre deux périodes.

    On compare les deux derniers ``ReportCard`` chronologiquement par
    ``AcademicPeriod.order``. Si |delta| > ``delta_threshold`` on lève
    l'anomalie. Severity HIGH parce qu'il s'agit souvent d'une erreur
    de saisie sur le bulletin ou d'une triche.
    """
    stmt = (
        select(
            ReportCard.studentId,
            ReportCard.average,
            ReportCard.periodId,
            AcademicPeriod.order.label("period_order"),
            Student.schoolId,
            School.regionId,
        )
        .join(AcademicPeriod, AcademicPeriod.id == ReportCard.periodId)
        .join(Student, Student.id == ReportCard.studentId)
        .join(School, School.id == Student.schoolId)
        .where(ReportCard.average.is_not(None))
        .order_by(ReportCard.studentId, AcademicPeriod.order)
        .limit(PER_DETECTOR_LIMIT * 4)  # marge : on agrège côté Python
    )
    if school_id is not None:
        stmt = stmt.where(Student.schoolId == school_id)

    rows = list((await session.execute(stmt)).all())

    out: list[AnomalyDetection] = []
    # Regroupement par étudiant (les rows sont déjà ordonnés)
    by_student: dict[str, list[Any]] = {}
    for r in rows:
        by_student.setdefault(r.studentId, []).append(r)

    for sid, items in by_student.items():
        if len(items) < 2:
            continue
        for i in range(1, len(items)):
            prev, curr = items[i - 1], items[i]
            delta = float(curr.average) - float(prev.average)
            if abs(delta) > delta_threshold:
                out.append(_make(
                    a_type=AnomalyType.GRADE_JUMP,
                    severity=AnomalySeverity.HIGH,
                    entity_type="Student",
                    entity_id=sid,
                    description=(
                        f"Saut de moyenne anormal : {prev.average:.2f} → "
                        f"{curr.average:.2f} (delta {delta:+.2f})."
                    ),
                    evidence={
                        "studentId": sid,
                        "previousAverage": float(prev.average),
                        "currentAverage": float(curr.average),
                        "delta": float(delta),
                        "previousPeriodId": prev.periodId,
                        "currentPeriodId": curr.periodId,
                        "thresholdAbs": delta_threshold,
                    },
                    school_id=curr.schoolId,
                    region_id=curr.regionId,
                ))
                if len(out) >= PER_DETECTOR_LIMIT:
                    return out
    return out


# ---------------------------------------------------------------------------
# 4. Date de naissance postérieure à la date d'inscription (createdAt)
# ---------------------------------------------------------------------------
async def detect_late_birthdate(
    session: AsyncSession, school_id: str | None = None,
) -> list[AnomalyDetection]:
    """Élève dont la date de naissance est postérieure à son inscription.

    Cas typique : doigt qui dérape sur l'année (2018 → 2028) ou
    inversion jour/mois. Severity CRITICAL — la donnée est manifestement
    invalide.
    """
    stmt = (
        select(
            Student.id, Student.birthDate, Student.createdAt,
            Student.schoolId, School.regionId,
        )
        .join(School, School.id == Student.schoolId)
        .where(
            Student.birthDate.is_not(None),
            Student.birthDate > Student.createdAt,
        )
        .limit(PER_DETECTOR_LIMIT)
    )
    if school_id is not None:
        stmt = stmt.where(Student.schoolId == school_id)

    rows = (await session.execute(stmt)).all()
    out: list[AnomalyDetection] = []
    for r in rows:
        out.append(_make(
            a_type=AnomalyType.INVALID_BIRTHDATE,
            severity=AnomalySeverity.CRITICAL,
            entity_type="Student",
            entity_id=r.id,
            description=(
                f"Date de naissance ({r.birthDate.isoformat()}) "
                f"postérieure à l'inscription ({r.createdAt.isoformat()})."
            ),
            evidence={
                "studentId": r.id,
                "birthDate": r.birthDate.isoformat(),
                "enrollmentDate": r.createdAt.isoformat(),
            },
            school_id=r.schoolId,
            region_id=r.regionId,
        ))
    return out


# ---------------------------------------------------------------------------
# 5. uniqueCode dupliqué (audit — devrait être impossible mais on vérifie)
# ---------------------------------------------------------------------------
async def detect_duplicate_codes(
    session: AsyncSession, school_id: str | None = None,
) -> list[AnomalyDetection]:
    """Doublons sur ``Student.uniqueCode`` (en théorie impossible — la
    colonne est ``UNIQUE`` — mais on garde un audit pour détecter une
    contrainte cassée ou un import incohérent).

    Severity HIGH — si ça se produit, la contrainte DB a sauté ou un
    seed test a contourné.
    """
    duplicate_codes_subq = (
        select(Student.uniqueCode)
        .group_by(Student.uniqueCode)
        .having(func.count() > 1)
        .scalar_subquery()
    )
    stmt = (
        select(
            Student.id, Student.uniqueCode,
            Student.schoolId, School.regionId,
        )
        .join(School, School.id == Student.schoolId)
        .where(Student.uniqueCode.in_(duplicate_codes_subq))
        .limit(PER_DETECTOR_LIMIT)
    )
    if school_id is not None:
        stmt = stmt.where(Student.schoolId == school_id)

    rows = (await session.execute(stmt)).all()
    out: list[AnomalyDetection] = []
    for r in rows:
        out.append(_make(
            a_type=AnomalyType.DUPLICATE_CODE,
            severity=AnomalySeverity.HIGH,
            entity_type="Student",
            entity_id=r.id,
            description=(
                f"uniqueCode '{r.uniqueCode}' présent sur plusieurs élèves "
                "— contrainte d'unicité violée."
            ),
            evidence={
                "studentId": r.id,
                "uniqueCode": r.uniqueCode,
            },
            school_id=r.schoolId,
            region_id=r.regionId,
        ))
    return out


# ---------------------------------------------------------------------------
# 6. Transferts excessifs (> 3 transferts pour un élève sur 1 an)
# ---------------------------------------------------------------------------
async def detect_excessive_transfers(
    session: AsyncSession,
    school_id: str | None = None,
    threshold: int = 3,
    window_days: int = 365,
) -> list[AnomalyDetection]:
    """Élève transféré plus de ``threshold`` fois en 1 an.

    Possible cas de fraude (manipulation des effectifs pour gonfler
    artificiellement les chiffres d'une école), ou symptôme d'un enfant
    en grande difficulté. Severity MEDIUM (audit).
    """
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    count_col = func.count(StudentTransfer.id)
    stmt = (
        select(
            StudentTransfer.studentId,
            count_col.label("n_transfers"),
            Student.schoolId,
            School.regionId,
        )
        .join(Student, Student.id == StudentTransfer.studentId)
        .join(School, School.id == Student.schoolId)
        .where(StudentTransfer.transferredAt >= cutoff)
        .group_by(StudentTransfer.studentId, Student.schoolId, School.regionId)
        .having(count_col > threshold)
        .limit(PER_DETECTOR_LIMIT)
    )
    if school_id is not None:
        stmt = stmt.where(
            or_(
                StudentTransfer.fromSchoolId == school_id,
                StudentTransfer.toSchoolId == school_id,
                Student.schoolId == school_id,
            )
        )

    rows = (await session.execute(stmt)).all()
    out: list[AnomalyDetection] = []
    for r in rows:
        out.append(_make(
            a_type=AnomalyType.EXCESSIVE_TRANSFER,
            severity=AnomalySeverity.MEDIUM,
            entity_type="Student",
            entity_id=r.studentId,
            description=(
                f"Élève transféré {r.n_transfers} fois en {window_days} "
                f"jours (seuil = {threshold})."
            ),
            evidence={
                "studentId": r.studentId,
                "transferCount": int(r.n_transfers),
                "thresholdMax": threshold,
                "windowDays": window_days,
            },
            school_id=r.schoolId,
            region_id=r.regionId,
        ))
    return out


# ---------------------------------------------------------------------------
# Registry — exposé au service pour lancer un run complet
# ---------------------------------------------------------------------------
ALL_DETECTORS = (
    detect_impossible_grades,
    detect_suspicious_attendance_100,
    detect_grade_jump,
    detect_late_birthdate,
    detect_duplicate_codes,
    detect_excessive_transfers,
)

# Silence linter (and_ imported for future detectors)
_ = and_

__all__ = [
    "ALL_DETECTORS",
    "PER_DETECTOR_LIMIT",
    "detect_duplicate_codes",
    "detect_excessive_transfers",
    "detect_grade_jump",
    "detect_impossible_grades",
    "detect_late_birthdate",
    "detect_suspicious_attendance_100",
]
