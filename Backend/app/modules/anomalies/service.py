"""Module 9 — AnomalyService : orchestration des détecteurs + workflow review."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationFailedError
from app.modules.anomalies.detectors import ALL_DETECTORS
from app.modules.anomalies.enums import (
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
)
from app.modules.anomalies.models import AnomalyDetection
from app.modules.anomalies.schemas import (
    AnomalyStats,
    AnomalyStatsBySeverity,
    AnomalyStatsByType,
)


class AnomalyService:
    """Service centralisé pour la détection et la revue d'anomalies."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------------------------------------------------------------------
    # Run
    # -------------------------------------------------------------------
    async def run_all_detectors(
        self, school_id: str | None = None,
    ) -> int:
        """Exécute TOUS les détecteurs et persiste les anomalies trouvées.

        Renvoie le nombre total d'anomalies créées. Les anomalies sont
        toutes créées avec ``status=PENDING`` (ce sera au directeur
        d'école de les confirmer / dismisser).
        """
        total = 0
        for detector in ALL_DETECTORS:
            results = await detector(self.session, school_id=school_id)
            for anomaly in results:
                self.session.add(anomaly)
                total += 1
            await self.session.flush()
        return total

    # -------------------------------------------------------------------
    # Listing
    # -------------------------------------------------------------------
    async def list_anomalies(
        self,
        *,
        status: AnomalyStatus | None = None,
        severity: AnomalySeverity | None = None,
        a_type: AnomalyType | None = None,
        school_id: str | None = None,
        region_id: str | None = None,
        entity_id: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[AnomalyDetection], int]:
        """Liste paginée. Renvoie (items, total)."""
        stmt = select(AnomalyDetection)
        count_stmt = select(func.count(AnomalyDetection.id))

        if status is not None:
            stmt = stmt.where(AnomalyDetection.status == status)
            count_stmt = count_stmt.where(AnomalyDetection.status == status)
        if severity is not None:
            stmt = stmt.where(AnomalyDetection.severity == severity)
            count_stmt = count_stmt.where(AnomalyDetection.severity == severity)
        if a_type is not None:
            stmt = stmt.where(AnomalyDetection.type == a_type)
            count_stmt = count_stmt.where(AnomalyDetection.type == a_type)
        if school_id is not None:
            stmt = stmt.where(AnomalyDetection.schoolId == school_id)
            count_stmt = count_stmt.where(AnomalyDetection.schoolId == school_id)
        if region_id is not None:
            stmt = stmt.where(AnomalyDetection.regionId == region_id)
            count_stmt = count_stmt.where(AnomalyDetection.regionId == region_id)
        if entity_id is not None:
            stmt = stmt.where(AnomalyDetection.entityId == entity_id)
            count_stmt = count_stmt.where(AnomalyDetection.entityId == entity_id)

        stmt = (
            stmt.order_by(AnomalyDetection.detectedAt.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = list((await self.session.execute(stmt)).scalars())
        total = int((await self.session.execute(count_stmt)).scalar_one() or 0)
        return items, total

    # -------------------------------------------------------------------
    # Detail
    # -------------------------------------------------------------------
    async def get_anomaly(self, anomaly_id: str) -> AnomalyDetection:
        row = await self.session.get(AnomalyDetection, anomaly_id)
        if row is None:
            raise NotFoundError(detail=f"Anomalie {anomaly_id} introuvable")
        return row

    # -------------------------------------------------------------------
    # Review
    # -------------------------------------------------------------------
    async def review_anomaly(
        self,
        anomaly_id: str,
        new_status: AnomalyStatus,
        note: str | None = None,
        reviewer_id: str | None = None,
    ) -> AnomalyDetection:
        """Passe une anomalie PENDING à CONFIRMED / DISMISSED / FALSE_POSITIVE.

        Refuse :
        * ``new_status == PENDING`` (pas de retour arrière).
        * Si l'anomalie est déjà revue (idempotence : on log mais on ne
          refuse pas — le directeur peut vouloir corriger sa décision).
        """
        if new_status == AnomalyStatus.PENDING:
            raise ValidationFailedError(
                detail="On ne peut pas remettre une anomalie en PENDING.",
            )
        anomaly = await self.get_anomaly(anomaly_id)
        anomaly.status = new_status
        anomaly.reviewedAt = datetime.now(UTC)
        anomaly.reviewedById = reviewer_id
        anomaly.reviewNote = note
        await self.session.flush()
        return anomaly

    # -------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------
    async def get_stats(
        self,
        *,
        school_id: str | None = None,
        region_id: str | None = None,
    ) -> AnomalyStats:
        """KPI agrégés par type, par sévérité, et taux de confirmation."""
        base_filter: list[Any] = []
        if school_id is not None:
            base_filter.append(AnomalyDetection.schoolId == school_id)
        if region_id is not None:
            base_filter.append(AnomalyDetection.regionId == region_id)

        # Total + par status
        status_stmt = (
            select(AnomalyDetection.status, func.count(AnomalyDetection.id))
            .group_by(AnomalyDetection.status)
        )
        for f in base_filter:
            status_stmt = status_stmt.where(f)
        status_rows = (await self.session.execute(status_stmt)).all()

        per_status: dict[str, int] = {row[0].value: int(row[1]) for row in status_rows}
        total = sum(per_status.values())
        pending = per_status.get(AnomalyStatus.PENDING.value, 0)
        confirmed = per_status.get(AnomalyStatus.CONFIRMED.value, 0)
        dismissed = per_status.get(AnomalyStatus.DISMISSED.value, 0)
        false_positive = per_status.get(AnomalyStatus.FALSE_POSITIVE.value, 0)
        reviewed = confirmed + dismissed + false_positive
        confirmation_rate = (confirmed / reviewed) if reviewed else 0.0

        # Par type
        type_stmt = (
            select(AnomalyDetection.type, func.count(AnomalyDetection.id))
            .group_by(AnomalyDetection.type)
        )
        for f in base_filter:
            type_stmt = type_stmt.where(f)
        type_rows = (await self.session.execute(type_stmt)).all()
        by_type = [
            AnomalyStatsByType(type=row[0], count=int(row[1]))
            for row in type_rows
        ]

        # Par sévérité
        sev_stmt = (
            select(AnomalyDetection.severity, func.count(AnomalyDetection.id))
            .group_by(AnomalyDetection.severity)
        )
        for f in base_filter:
            sev_stmt = sev_stmt.where(f)
        sev_rows = (await self.session.execute(sev_stmt)).all()
        by_sev = [
            AnomalyStatsBySeverity(severity=row[0], count=int(row[1]))
            for row in sev_rows
        ]

        return AnomalyStats(
            total=total,
            pending=pending,
            confirmed=confirmed,
            dismissed=dismissed,
            falsePositive=false_positive,
            byType=by_type,
            bySeverity=by_sev,
            confirmationRate=round(confirmation_rate, 4),
        )


__all__ = ["AnomalyService"]
