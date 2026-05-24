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
# 7. Module 1B — point chaud GPI (filles/garçons < 0.85 sur une école)
# ---------------------------------------------------------------------------
async def detect_critical_gpi(
    session: AsyncSession,
    school_year_id: str | None = None,
) -> list[AnomalyDetection]:
    """Détecte les écoles avec un GPI critique (< 0.85) sur une année donnée.

    Lit la table ``GpiSnapshot`` (alimentée par ``EnrollmentService``
    .compute_gpi_snapshots) et matérialise une anomalie par école touchée.
    Severity HIGH (objectif gouvernemental "améliorer la scolarisation
    des filles" — chaque école sous le seuil doit être suivie).

    Différences vs les autres détecteurs
    -----------------------------------
    * Pas de balayage SQL "à la volée" : on s'appuie sur la table de
      snapshots persistée — sinon il faudrait reconstruire l'agrégation
      filles/garçons à chaque détection (déjà fait par le service GPI).
    * ``school_year_id`` est obligatoire (sinon : dernière année disponible
      par snapshot, ce qui devient ambigu en pratique). Le détecteur est
      typiquement appelé en hook post-``compute_gpi_snapshots`` et reçoit
      l'année qui vient d'être recalculée.
    """
    # Import local pour éviter la dépendance cyclique au module enrollment
    # (le module enrollment dépend du module anomalies pour pousser).
    from app.modules.enrollment.enums import GpiScope
    from app.modules.enrollment.models import GpiSnapshot
    from app.modules.enrollment.parity import GpiSeverity
    from app.modules.schools.models import School as _School

    stmt = (
        select(
            GpiSnapshot.entityId,
            GpiSnapshot.gpi,
            GpiSnapshot.girlsCount,
            GpiSnapshot.boysCount,
            _School.regionId,
        )
        .join(_School, _School.id == GpiSnapshot.entityId)
        .where(
            GpiSnapshot.scope == GpiScope.SCHOOL,
            GpiSnapshot.severity == GpiSeverity.CRITICAL_GIRLS,
        )
        .limit(PER_DETECTOR_LIMIT)
    )
    if school_year_id is not None:
        stmt = stmt.where(GpiSnapshot.schoolYearId == school_year_id)

    rows = (await session.execute(stmt)).all()
    out: list[AnomalyDetection] = []
    for r in rows:
        gpi_value = float(r.gpi) if r.gpi is not None else None
        out.append(_make(
            a_type=AnomalyType.CRITICAL_GPI,
            severity=AnomalySeverity.HIGH,
            entity_type="School",
            entity_id=r.entityId,
            description=(
                "Indice de parité fille/garçon critique (< 0.85) — "
                "objectif gouvernemental « améliorer la scolarisation des "
                "filles » non atteint."
            ),
            evidence={
                "schoolId": r.entityId,
                "schoolYearId": school_year_id,
                "gpi": gpi_value,
                "girlsCount": int(r.girlsCount),
                "boysCount": int(r.boysCount),
                "thresholdMax": 0.85,
            },
            school_id=r.entityId,
            region_id=r.regionId,
        ))
    return out


# ---------------------------------------------------------------------------
# 8. Module 1C — Écart de GPI urbain vs rural (> 0.10) sur une région
# ---------------------------------------------------------------------------
async def detect_urban_rural_gpi_gap(
    session: AsyncSession,
    school_year_id: str,
    *,
    delta_threshold: float = 0.10,
) -> list[AnomalyDetection]:
    """Détecte les régions où |GPI urbain - GPI rural| > seuil.

    Lit les ``Enrollment`` ``CENSUS_DECLARED`` pour la year et calcule par
    région le GPI dans chacune des deux zones (URBAN vs RURAL), via
    ``COALESCE(School.zoneType, SubPrefecture.defaultZoneType)``. Une
    région où l'écart dépasse ``delta_threshold`` (par défaut 0.10) est
    matérialisée en anomalie HIGH.

    On compare uniquement URBAN vs RURAL : le PERI_URBAN est exposé dans
    les KPI cockpit mais ne déclenche pas d'alerte (zone tampon, valeur
    informative).
    """
    from decimal import Decimal as _Decimal

    from app.modules.enrollment.enums import EnrollmentSource
    from app.modules.enrollment.models import Enrollment
    from app.modules.territory.models import Region as _Region
    from app.modules.territory.models import SubPrefecture as _SubPref
    from app.shared.enums import Gender as _Gender
    from app.shared.enums import ZoneType as _ZoneType

    effective_zone = func.coalesce(
        School.zoneType, _SubPref.defaultZoneType,
    ).label("effective_zone")

    stmt = (
        select(
            School.regionId,
            _Region.name.label("region_name"),
            effective_zone,
            Enrollment.gender,
            func.coalesce(func.sum(Enrollment.count), 0).label("total"),
        )
        .select_from(Enrollment)
        .join(School, School.id == Enrollment.schoolId)
        .outerjoin(_SubPref, _SubPref.id == School.subPrefectureId)
        .join(_Region, _Region.id == School.regionId)
        .where(
            Enrollment.schoolYearId == school_year_id,
            Enrollment.source == EnrollmentSource.CENSUS_DECLARED,
        )
        .group_by(
            School.regionId, _Region.name,
            effective_zone, Enrollment.gender,
        )
        .limit(PER_DETECTOR_LIMIT * 10)
    )
    rows = (await session.execute(stmt)).all()

    # Reconstitue par (regionId, zone) -> {gender: count}.
    by_region_zone: dict[tuple[str, _ZoneType], dict[_Gender, int]] = {}
    region_names: dict[str, str] = {}
    for region_id, region_name, zone_raw, gender, total in rows:
        zone = (
            _ZoneType(zone_raw)
            if zone_raw is not None
            else _ZoneType.RURAL
        )
        key = (region_id, zone)
        entry = by_region_zone.setdefault(
            key, {_Gender.FEMALE: 0, _Gender.MALE: 0},
        )
        if gender in (_Gender.FEMALE, _Gender.MALE):
            entry[gender] += int(total)
        region_names[region_id] = region_name

    def _gpi(g: int, b: int) -> _Decimal | None:
        if b <= 0:
            return None
        return (_Decimal(g) / _Decimal(b)).quantize(_Decimal("0.0001"))

    out: list[AnomalyDetection] = []
    region_ids = {key[0] for key in by_region_zone}
    for region_id in region_ids:
        urban = by_region_zone.get((region_id, _ZoneType.URBAN))
        rural = by_region_zone.get((region_id, _ZoneType.RURAL))
        if not urban or not rural:
            # Pas comparable : il faut des effectifs dans les 2 zones.
            continue
        urban_gpi = _gpi(urban[_Gender.FEMALE], urban[_Gender.MALE])
        rural_gpi = _gpi(rural[_Gender.FEMALE], rural[_Gender.MALE])
        if urban_gpi is None or rural_gpi is None:
            continue
        delta = abs(urban_gpi - rural_gpi)
        if float(delta) <= delta_threshold:
            continue
        out.append(_make(
            a_type=AnomalyType.URBAN_RURAL_GPI_GAP,
            severity=AnomalySeverity.HIGH,
            entity_type="Region",
            entity_id=region_id,
            description=(
                f"Écart GPI urbain/rural dans la région {region_names.get(region_id, region_id)} : "
                f"|{urban_gpi} - {rural_gpi}| = {delta} > seuil {delta_threshold}."
            ),
            evidence={
                "regionId": region_id,
                "regionName": region_names.get(region_id),
                "schoolYearId": school_year_id,
                "urbanGpi": float(urban_gpi),
                "ruralGpi": float(rural_gpi),
                "deltaGpi": float(delta),
                "thresholdMax": delta_threshold,
            },
            region_id=region_id,
        ))
        if len(out) >= PER_DETECTOR_LIMIT:
            break
    return out


# ---------------------------------------------------------------------------
# 9. Module 2A — Taux de transition aberrant (rate > 2 OU rate < 0.5)
# ---------------------------------------------------------------------------
async def detect_transition_rate_outliers(
    session: AsyncSession,
    *,
    outlier_rows: list[Any] | None = None,
    school_year_from_id: str | None = None,
) -> list[AnomalyDetection]:
    """Détecte les taux de transition aberrants par région.

    Critère anomalie (plus strict que le ``isOutlier`` stocké en DB) :
    ``rate > 2`` (redoublement de masse / erreur saisie) OU ``rate < 0.5``
    (signal d'abandons massifs entre deux niveaux scolaires successifs).

    Severity ``MEDIUM`` — signal à investiguer (pas un blocage métier).
    ``entityType = "Region"``.

    Deux modes d'appel :
    * ``outlier_rows`` fourni — le service vient de calculer les rates,
      on évite une 2e requête DB et on filtre directement la liste.
    * Sinon, on requête ``TransitionRate`` filtré sur isOutlier OR
      rate < 0.5 (optionnellement scopé sur une year_from précise).
    """
    # Import local pour éviter la dépendance cyclique au module projections.
    from app.modules.projections.models import TransitionRate

    if outlier_rows is None:
        from decimal import Decimal as _Decimal

        stmt = select(TransitionRate).where(
            or_(
                TransitionRate.isOutlier.is_(True),
                TransitionRate.rate < _Decimal("0.5"),
            )
        )
        if school_year_from_id is not None:
            stmt = stmt.where(
                TransitionRate.schoolYearFromId == school_year_from_id,
            )
        stmt = stmt.limit(PER_DETECTOR_LIMIT)
        outlier_rows = list(
            (await session.execute(stmt)).scalars().all(),
        )

    out: list[AnomalyDetection] = []
    for r in outlier_rows:
        # Critère anomalie : rate hors [0.5, 2.0] (NULL skip).
        if r.rate is None:
            continue
        rate_float = float(r.rate)
        if 0.5 <= rate_float <= 2.0:
            continue

        signal = (
            "redoublement de masse / erreur saisie"
            if rate_float > 2.0
            else "abandons massifs"
        )
        # entityId : region pour REGIONAL, "NATIONAL" symbolique pour
        # NATIONAL (l'anomalie reste rattachée à un agrégat — utile pour
        # le triage cabinet).
        entity_id = r.entityId or "NATIONAL"
        out.append(_make(
            a_type=AnomalyType.TRANSITION_RATE_OUTLIER,
            severity=AnomalySeverity.MEDIUM,
            entity_type="Region",
            entity_id=entity_id,
            description=(
                f"Taux de transition {r.classLevelFrom.value}→"
                f"{r.classLevelTo.value} ({r.gender.value}) = "
                f"{rate_float:.4f} — {signal} "
                f"(sample={r.sampleSize})."
            ),
            evidence={
                "transitionRateId": r.id,
                "scope": r.scope.value,
                "regionId": r.entityId,
                "schoolYearFromId": r.schoolYearFromId,
                "schoolYearToId": r.schoolYearToId,
                "classLevelFrom": r.classLevelFrom.value,
                "classLevelTo": r.classLevelTo.value,
                "gender": r.gender.value,
                "rate": rate_float,
                "sampleSize": int(r.sampleSize),
                "thresholdMax": 2.0,
                "thresholdMin": 0.5,
            },
            region_id=r.entityId if r.scope.value == "REGIONAL" else None,
        ))
        if len(out) >= PER_DETECTOR_LIMIT:
            break
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
    "detect_critical_gpi",
    "detect_duplicate_codes",
    "detect_excessive_transfers",
    "detect_grade_jump",
    "detect_impossible_grades",
    "detect_late_birthdate",
    "detect_suspicious_attendance_100",
    "detect_transition_rate_outliers",
    "detect_urban_rural_gpi_gap",
]
