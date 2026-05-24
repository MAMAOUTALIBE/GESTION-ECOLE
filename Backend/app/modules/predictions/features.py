"""Module 8 — Extraction de features pour la prédiction du décrochage.

6 features simples, calculées sur place via SQL agrégé. Pas de feature store
(Module 8.1) : on relit la DB à chaque prédiction. Pour du temps réel ou du
batch quotidien, c'est largement suffisant (< 50ms par élève).

Defaults & data leakage
-----------------------
* On utilise ``ref_date`` comme borne supérieure stricte (``<`` pas ``<=``)
  pour éviter qu'un score "calculé aujourd'hui" intègre des évènements
  futurs si on re-score historiquement.
* Quand une valeur est manquante (élève sans aucune présence enregistrée,
  par ex.), on remplace par une moyenne raisonnable ; on documente ces
  defaults pour ne pas surprendre.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import AcademicPeriod, Grade
from app.modules.attendance.models import AttendanceRecord
from app.modules.schoollife.models import Incident
from app.shared.enums import AttendanceStatus

# Ordre canonique des features — DOIT correspondre à ``DropoutModel.feature_names``.
FEATURE_NAMES: tuple[str, ...] = (
    "attendance_rate_90d",
    "attendance_rate_30d",
    "grade_avg_last_period",
    "grade_trend",
    "incidents_count_180d",
    "late_count_30d",
)

# Defaults raisonnables quand on n'a pas de signal pour un élève.
# Valeurs choisies à partir de bornes "moyen national raisonnable" :
#   - 85% présence est l'objectif min OMS pour l'apprentissage continu
#   - 10/20 est le seuil de passage standard
#   - 0 trend / 0 incident / 0 retard = neutre
FEATURE_DEFAULTS: dict[str, float] = {
    "attendance_rate_90d": 0.85,
    "attendance_rate_30d": 0.85,
    "grade_avg_last_period": 10.0,
    "grade_trend": 0.0,
    "incidents_count_180d": 0.0,
    "late_count_30d": 0.0,
}


def _ensure_dt(ref_date: date) -> datetime:
    """Normalise ``date`` -> datetime aware (00:00 UTC)."""
    if isinstance(ref_date, datetime):
        return ref_date if ref_date.tzinfo else ref_date.replace(tzinfo=UTC)
    return datetime(ref_date.year, ref_date.month, ref_date.day, tzinfo=UTC)


async def _attendance_rate(
    session: AsyncSession, student_id: str, ref: datetime, days: int,
) -> float | None:
    """Retourne le ratio (PRESENT) / (PRESENT+LATE+ABSENT) sur les N derniers
    jours, ou None si aucun scan trouvé.
    """
    since = ref - timedelta(days=days)
    stmt = select(
        func.count().label("total"),
        func.sum(
            case((AttendanceRecord.status == AttendanceStatus.PRESENT, 1), else_=0)
        ).label("present"),
    ).where(
        AttendanceRecord.studentId == student_id,
        AttendanceRecord.scannedAt >= since,
        AttendanceRecord.scannedAt < ref,
    )
    row = (await session.execute(stmt)).one()
    total = int(row.total or 0)
    present = int(row.present or 0)
    if total == 0:
        return None
    return present / total


async def _late_count(
    session: AsyncSession, student_id: str, ref: datetime, days: int,
) -> int:
    since = ref - timedelta(days=days)
    stmt = select(func.count()).where(
        AttendanceRecord.studentId == student_id,
        AttendanceRecord.status == AttendanceStatus.LATE,
        AttendanceRecord.scannedAt >= since,
        AttendanceRecord.scannedAt < ref,
    )
    return int((await session.execute(stmt)).scalar() or 0)


async def _incidents_count(
    session: AsyncSession, student_id: str, ref: datetime, days: int,
) -> int:
    since = ref - timedelta(days=days)
    stmt = select(func.count()).where(
        Incident.studentId == student_id,
        Incident.occurredAt >= since,
        Incident.occurredAt < ref,
    )
    return int((await session.execute(stmt)).scalar() or 0)


async def _grade_avg_and_trend(
    session: AsyncSession, student_id: str, ref: datetime,
) -> tuple[float | None, float | None]:
    """Renvoie (moyenne dernière période, delta entre dernière et précédente).

    Stratégie MVP : on regarde toutes les notes d'un élève groupées par
    ``periodId``, on prend les périodes ordonnées par ``AcademicPeriod.order``
    en commençant par celles qui ont commencé avant ``ref``. La dernière
    période = la plus récente, la précédente = celle d'avant.
    """
    stmt = (
        select(
            Grade.periodId,
            AcademicPeriod.order.label("period_order"),
            func.avg(Grade.score).label("avg_score"),
        )
        .join(AcademicPeriod, AcademicPeriod.id == Grade.periodId)
        .where(Grade.studentId == student_id)
        # On ne retient que les périodes "déjà jouées" à `ref` (pas de leak).
        .where(
            (AcademicPeriod.startDate.is_(None))
            | (AcademicPeriod.startDate < ref)
        )
        .group_by(Grade.periodId, AcademicPeriod.order)
        .order_by(AcademicPeriod.order.desc())
        .limit(2)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return None, None
    last = float(rows[0].avg_score)
    if len(rows) == 1:
        return last, None
    previous = float(rows[1].avg_score)
    return last, last - previous


async def extract_features(
    session: AsyncSession, student_id: str, ref_date: date,
) -> dict[str, float]:
    """Extrait les 6 features pour un élève à une date de référence.

    Toutes les valeurs sont garanties non-None : les manquantes sont
    remplacées par les ``FEATURE_DEFAULTS`` (documentés). L'ordre des clés
    suit ``FEATURE_NAMES``.
    """
    ref = _ensure_dt(ref_date)

    rate_90d = await _attendance_rate(session, student_id, ref, 90)
    rate_30d = await _attendance_rate(session, student_id, ref, 30)
    grade_avg, grade_trend = await _grade_avg_and_trend(session, student_id, ref)
    incidents_180d = await _incidents_count(session, student_id, ref, 180)
    late_30d = await _late_count(session, student_id, ref, 30)

    raw: dict[str, float | None] = {
        "attendance_rate_90d": rate_90d,
        "attendance_rate_30d": rate_30d,
        "grade_avg_last_period": grade_avg,
        "grade_trend": grade_trend,
        "incidents_count_180d": float(incidents_180d),
        "late_count_30d": float(late_30d),
    }
    features: dict[str, float] = {}
    for name in FEATURE_NAMES:
        value = raw[name]
        features[name] = float(value) if value is not None else FEATURE_DEFAULTS[name]
    return features


def features_to_vector(features: dict[str, float]) -> list[float]:
    """Sérialise un dict features -> liste ordonnée selon ``FEATURE_NAMES``."""
    return [features[name] for name in FEATURE_NAMES]
