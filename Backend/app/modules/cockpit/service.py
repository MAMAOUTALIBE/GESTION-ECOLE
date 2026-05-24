"""Module 19 — CockpitService : KPI nationaux + top alertes + briefing.

Stratégie
---------
* Toutes les méthodes "agrégation lourde" cachent leur résultat 30s en
  Redis (clef ``cockpit:<méthode>[:args]``). On évite ainsi qu'un cabinet
  ministre rechargeant son dashboard toutes les 5s ne génère 200 COUNT(*)
  par minute sur la base. Le TTL court (30s) garantit que la fraîcheur
  reste perçue comme "live" par le décideur.
* Les COUNT parallèles utilisent ``asyncio.gather`` pour éviter le wall
  clock cumulé. Tout calcul KPI est isolé dans une coroutine privée :
  on peut donc échouer un KPI sans casser les autres (chaque coroutine
  catche son exception et renvoie 0 + log).
* Le briefing quotidien est généré via Claude (Module 10) SI
  ``ANTHROPIC_API_KEY`` est présente, sinon via un template français
  factuel (mode dégradé déterministe, idéal pour les environnements
  air-gapped / staging).
* Les snapshots quotidiens écrivent dans ``CockpitKpiSnapshot`` (1 ligne
  par (date, kpiKey, scope)). L'idempotence est garantie par un UPSERT
  "delete then insert" pour la même date (pas de contrainte unique car
  on veut tolérer l'évolution du schéma sans casser les writes ; le
  service garde la dernière valeur).
"""
from __future__ import annotations

import asyncio
import json
import os
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_redis
from app.modules.anomalies.enums import AnomalySeverity, AnomalyStatus
from app.modules.anomalies.models import AnomalyDetection
from app.modules.attendance.models import AttendanceRecord
from app.modules.census.models import Student
from app.modules.cockpit.enums import (
    AlertSeverity,
    CockpitScope,
    KpiKey,
)
from app.modules.cockpit.models import CockpitKpiSnapshot
from app.modules.cockpit.schemas import (
    BriefingAlertItem,
    BriefingResponse,
    ComparisonResponse,
    NationalKpiResponse,
    SnapshotRunResponse,
    TimeSeriesPoint,
    TimeSeriesResponse,
    TopAlertRegionRow,
    TopAlertSchoolRow,
    TopAlertsResponse,
    UrbanRuralGapResponse,
)
from app.modules.finance.models import Budget, Expense
from app.modules.predictions.enums import DropoutRiskLevel
from app.modules.predictions.models import DropoutPrediction
from app.modules.schools.models import School
from app.modules.territory.models import Region
from app.shared.enums import AttendanceStatus

CACHE_TTL_SECONDS = 30
CACHE_KEY_PREFIX = "cockpit"


def _cache_key(*parts: str) -> str:
    return ":".join((CACHE_KEY_PREFIX, *parts))


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _today_utc() -> date:
    return _now_utc().date()


class CockpitService:
    """Service d'agrégation cockpit (read-mostly, écriture quotidienne).

    Le service est volontairement stateful sur la session (passée au ctor)
    pour rester aligné avec le reste de la codebase (cf. AnomalyService,
    FinanceService, AnalyticsService).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # NATIONAL KPIs (cached)
    # ==================================================================
    async def get_national_kpis(self) -> NationalKpiResponse:
        """Agrège les KPI nationaux. Cache Redis 30s."""
        cached = await self._cache_get("kpis:national")
        if cached is not None:
            # On override `cached` à True dans la copie pour que le caller
            # sache que la valeur vient du cache.
            cached["cached"] = True
            return NationalKpiResponse(**cached)

        # Coroutines isolées — chaque exception est neutralisée pour ne
        # pas faire tomber le payload entier.
        (
            students_total,
            attendance_rate,
            budget_consumption,
            critical_anomalies,
            alerts_open,
            national_gpi,
            urban_rural_gap,
            projected_critical_schools,
        ) = await asyncio.gather(
            self._count_students(),
            self._compute_attendance_rate_recent(),
            self._compute_budget_consumption(),
            self._count_critical_open_anomalies(),
            self._count_alerts_open(),
            self._compute_national_gpi(),
            self._compute_urban_rural_gap_latest_year(),
            self._count_projected_critical_schools(),
            return_exceptions=False,
        )

        response = NationalKpiResponse(
            studentsTotal=int(students_total),
            attendanceRate=round(float(attendance_rate), 2),
            budgetConsumption=round(float(budget_consumption), 2),
            criticalAnomaliesOpen=int(critical_anomalies),
            alertsOpen=int(alerts_open),
            nationalGpi=national_gpi,
            urbanRuralGap=urban_rural_gap,
            projectedCriticalSchools=int(projected_critical_schools),
            items={
                KpiKey.STUDENTS_TOTAL.value: float(students_total),
                KpiKey.ATTENDANCE_RATE.value: round(float(attendance_rate), 2),
                KpiKey.BUDGET_CONSUMPTION.value: round(float(budget_consumption), 2),
                KpiKey.CRITICAL_ANOMALIES_OPEN.value: float(critical_anomalies),
                KpiKey.ALERTS_OPEN.value: float(alerts_open),
                # Module 1B — GPI national (None encodé en 0.0 dans items
                # pour rester compatible avec dict[str, float] ; le champ
                # ``nationalGpi`` typé Decimal|None reste la source de vérité).
                KpiKey.NATIONAL_GPI.value: (
                    float(national_gpi) if national_gpi is not None else 0.0
                ),
                # Module 2C — Écoles CRITICAL sur projection +1 an.
                KpiKey.PROJECTED_CRITICAL_SCHOOLS_COUNT.value: float(
                    projected_critical_schools,
                ),
            },
            generatedAt=_now_utc(),
            cached=False,
        )
        await self._cache_set("kpis:national", response.model_dump(mode="json"))
        return response

    async def _compute_urban_rural_gap_latest_year(
        self,
    ) -> UrbanRuralGapResponse | None:
        """Calcule l'écart urbain/rural sur la dernière année scolaire active.

        Retourne ``None`` si aucune SchoolYear active ou aucun effectif
        déclaré (cas démarrage à froid). Ce calcul est wrappé d'un
        try/except : tout échec retourne ``None`` plutôt que de faire
        tomber le payload KPI complet (cf. convention du service).
        """
        try:
            from app.modules.academics.models import SchoolYear

            stmt = (
                select(SchoolYear.id)
                .where(SchoolYear.isActive == True)  # noqa: E712
                .order_by(SchoolYear.startDate.desc())
                .limit(1)
            )
            active_year = (await self.session.execute(stmt)).scalar_one_or_none()
            if active_year is None:
                return None
            return await self.get_urban_rural_gap(active_year)
        except Exception as exc:
            logger.warning(
                "cockpit._compute_urban_rural_gap_latest_year failed: {}", exc,
            )
            return None

    # ==================================================================
    # TOP ALERTS
    # ==================================================================
    async def get_top_alerts(self, limit: int = 10) -> TopAlertsResponse:
        """Top N écoles + N régions sur la base des anomalies / dropouts."""
        cached = await self._cache_get(f"top_alerts:{limit}")
        if cached is not None:
            return TopAlertsResponse(**cached)

        schools, regions = await asyncio.gather(
            self._top_schools_by_anomalies(limit),
            self._top_regions_by_dropout(limit),
        )

        response = TopAlertsResponse(
            schools=schools,
            regions=regions,
            generatedAt=_now_utc(),
        )
        await self._cache_set(
            f"top_alerts:{limit}", response.model_dump(mode="json"),
        )
        return response

    async def _top_schools_by_anomalies(
        self, limit: int,
    ) -> list[TopAlertSchoolRow]:
        # On compte les anomalies PENDING (= ouvertes) par schoolId, on join
        # School pour récupérer le nom, on trie desc, on limit.
        stmt = (
            select(
                School.id.label("school_id"),
                School.name.label("school_name"),
                School.regionId.label("region_id"),
                func.count(AnomalyDetection.id).label("anomalies_count"),
            )
            .join(AnomalyDetection, AnomalyDetection.schoolId == School.id)
            .where(AnomalyDetection.status == AnomalyStatus.PENDING)
            .group_by(School.id, School.name, School.regionId)
            .order_by(desc("anomalies_count"))
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            TopAlertSchoolRow(
                schoolId=r.school_id,
                schoolName=r.school_name,
                anomaliesCount=int(r.anomalies_count),
                regionId=r.region_id,
            )
            for r in rows
        ]

    async def _top_regions_by_dropout(
        self, limit: int,
    ) -> list[TopAlertRegionRow]:
        # Decrochage = DropoutPrediction.riskLevel == HIGH, agrégé par
        # region via Student -> School.regionId.
        stmt = (
            select(
                Region.id.label("region_id"),
                Region.name.label("region_name"),
                func.count(DropoutPrediction.id).label("dropout_count"),
            )
            .join(Student, Student.id == DropoutPrediction.studentId)
            .join(School, School.id == Student.schoolId)
            .join(Region, Region.id == School.regionId)
            .where(DropoutPrediction.riskLevel == DropoutRiskLevel.HIGH)
            .group_by(Region.id, Region.name)
            .order_by(desc("dropout_count"))
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            TopAlertRegionRow(
                regionId=r.region_id,
                regionName=r.region_name,
                dropoutCount=int(r.dropout_count),
            )
            for r in rows
        ]

    # ==================================================================
    # TIME SERIES
    # ==================================================================
    async def get_attendance_timeseries(
        self, days: int = 90,
    ) -> TimeSeriesResponse:
        """Présence nationale jour par jour sur N jours (par défaut 90)."""
        cached = await self._cache_get(f"ts:attendance:{days}")
        if cached is not None:
            return TimeSeriesResponse(**cached)

        cutoff = _now_utc() - timedelta(days=days)
        # Agrégation par jour : on dispatche 2 sub-queries (présents + total)
        # plutôt qu'un SUM(CASE WHEN ...) — plus portable, plus lisible et
        # exécutées en parallèle via asyncio.gather ci-dessous.
        day_col = func.date(AttendanceRecord.scannedAt)
        present_stmt = (
            select(
                day_col.label("d"),
                func.count().label("c"),
            )
            .where(
                and_(
                    AttendanceRecord.scannedAt >= cutoff,
                    AttendanceRecord.status == AttendanceStatus.PRESENT,
                )
            )
            .group_by(day_col)
        )
        total_stmt = (
            select(
                day_col.label("d"),
                func.count().label("c"),
            )
            .where(AttendanceRecord.scannedAt >= cutoff)
            .group_by(day_col)
        )

        present_rows, total_rows = await asyncio.gather(
            self.session.execute(present_stmt),
            self.session.execute(total_stmt),
        )
        present_by_day = {self._coerce_date(r.d): int(r.c) for r in present_rows.all()}
        total_by_day = {self._coerce_date(r.d): int(r.c) for r in total_rows.all()}

        # Génère la série complète (zéros inclus) pour les jours sans data.
        points: list[TimeSeriesPoint] = []
        today = _today_utc()
        start = today - timedelta(days=days - 1)
        for offset in range(days):
            d = start + timedelta(days=offset)
            total = total_by_day.get(d, 0)
            present = present_by_day.get(d, 0)
            rate = (present / total * 100.0) if total else 0.0
            points.append(
                TimeSeriesPoint(date=d, value=round(rate, 2)),
            )

        response = TimeSeriesResponse(
            kpiKey=KpiKey.ATTENDANCE_RATE.value,
            granularity="DAY",
            points=points,
            generatedAt=_now_utc(),
        )
        await self._cache_set(
            f"ts:attendance:{days}", response.model_dump(mode="json"),
        )
        return response

    async def get_anomaly_timeseries(
        self, weeks: int = 12,
    ) -> TimeSeriesResponse:
        """Anomalies semaine par semaine sur N semaines (12 par défaut)."""
        cached = await self._cache_get(f"ts:anomalies:{weeks}")
        if cached is not None:
            return TimeSeriesResponse(**cached)

        cutoff = _now_utc() - timedelta(days=weeks * 7)
        # On agrège côté Python pour éviter les pièges de fuseau horaire
        # autour de date_trunc('week', ...) (qui renvoie un timestamp UTC
        # qui se "décale" lors du .date() en présence d'offset). On groupe
        # par ISO week côté serveur applicatif : pour les volumes attendus
        # (< 100k anomalies sur 12 semaines), l'overhead est négligeable.
        stmt = (
            select(AnomalyDetection.detectedAt)
            .where(AnomalyDetection.detectedAt >= cutoff)
        )
        rows = (await self.session.execute(stmt)).all()

        # Génère N semaines (zéros inclus) — alignées sur le lundi ISO.
        today = _today_utc()
        monday_today = today - timedelta(days=today.weekday())
        by_week: dict[date, int] = {}
        for r in rows:
            d = r[0]
            d_date = d.date() if hasattr(d, "date") else d
            week_start = d_date - timedelta(days=d_date.weekday())
            by_week[week_start] = by_week.get(week_start, 0) + 1

        points: list[TimeSeriesPoint] = []
        for i in range(weeks - 1, -1, -1):
            d = monday_today - timedelta(days=7 * i)
            points.append(
                TimeSeriesPoint(
                    date=d,
                    value=float(by_week.get(d, 0)),
                    label=f"S-{d.isocalendar().week:02d}",
                ),
            )

        response = TimeSeriesResponse(
            kpiKey="ANOMALIES_PER_WEEK",
            granularity="WEEK",
            points=points,
            generatedAt=_now_utc(),
        )
        await self._cache_set(
            f"ts:anomalies:{weeks}", response.model_dump(mode="json"),
        )
        return response

    # ==================================================================
    # BRIEFING
    # ==================================================================
    async def generate_briefing(
        self, briefing_date: date | None = None,
    ) -> BriefingResponse:
        """Génère le briefing quotidien.

        Mode LLM si ``ANTHROPIC_API_KEY`` présente, sinon template
        déterministe (utile en CI / environnement air-gapped).
        """
        briefing_date = briefing_date or _today_utc()

        # On ré-utilise les KPI cached.
        kpis = await self.get_national_kpis()
        top = await self.get_top_alerts(limit=3)

        alerts: list[BriefingAlertItem] = [
            BriefingAlertItem(
                schoolId=s.schoolId,
                schoolName=s.schoolName,
                severity=AlertSeverity.CRITICAL if s.anomaliesCount > 5
                else AlertSeverity.HIGH,
                summary=(
                    f"{s.anomaliesCount} anomalie(s) en attente"
                    + (f" — {s.schoolName}" if s.schoolName else "")
                ),
            )
            for s in top.schools[:3]
        ]

        # Mode LLM si la clé API est dispo.
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if api_key:
            try:
                headline, bullets = await self._llm_briefing(
                    api_key=api_key,
                    briefing_date=briefing_date,
                    kpis=kpis,
                    alerts=alerts,
                )
                source = "llm"
            except Exception as exc:  # pragma: no cover - LLM net error
                logger.warning(
                    "cockpit.generate_briefing: LLM failed, fallback template: {}",
                    exc,
                )
                headline, bullets = self._template_briefing(
                    briefing_date, kpis, alerts,
                )
                source = "template"
        else:
            headline, bullets = self._template_briefing(
                briefing_date, kpis, alerts,
            )
            source = "template"

        return BriefingResponse(
            date=briefing_date,
            headline=headline,
            bullets=bullets,
            topAlerts=alerts,
            kpis=kpis.items,
            source=source,
            generatedAt=_now_utc(),
        )

    def _template_briefing(
        self,
        briefing_date: date,
        kpis: NationalKpiResponse,
        alerts: list[BriefingAlertItem],
    ) -> tuple[str, list[str]]:
        """Mode dégradé déterministe : assemble un brief factuel français."""
        date_fr = briefing_date.strftime("%d/%m/%Y")
        headline = (
            f"Brief du {date_fr} — {kpis.studentsTotal} élèves suivis, "
            f"présence {kpis.attendanceRate:.1f}%."
        )
        bullets = [
            f"Élèves suivis aujourd'hui : {kpis.studentsTotal}.",
            f"Taux de présence (7 derniers jours) : {kpis.attendanceRate:.1f}%.",
            f"Budget consommé : {kpis.budgetConsumption:.1f}%.",
            f"Anomalies critiques ouvertes : {kpis.criticalAnomaliesOpen}.",
            f"Alertes ouvertes : {kpis.alertsOpen}.",
        ]
        if alerts:
            bullets.append(
                "Top alertes : "
                + ", ".join(a.summary for a in alerts[:3]),
            )
        return headline, bullets

    async def _llm_briefing(
        self,
        *,
        api_key: str,
        briefing_date: date,
        kpis: NationalKpiResponse,
        alerts: list[BriefingAlertItem],
    ) -> tuple[str, list[str]]:
        """Appelle Claude Haiku pour générer un brief structuré.

        On force le format JSON via le system prompt et on parse côté
        serveur (pas de tool-use ici — un seul tour, latence minimale).
        """
        from anthropic import AsyncAnthropic  # type: ignore[import-untyped]

        client = AsyncAnthropic(api_key=api_key)
        sys_prompt = (
            "Tu rédiges un brief ministériel quotidien en français pour le "
            "cabinet du Ministre de l'Éducation. Réponds EXCLUSIVEMENT par "
            "un JSON valide : {\"headline\": str, \"bullets\": [str, str, ...]}."
            " Bullets : 4-6 phrases courtes, factuelles, sans inventer de chiffre."
        )
        payload = {
            "date": briefing_date.isoformat(),
            "kpis": kpis.items,
            "topAlerts": [a.model_dump() for a in alerts],
        }
        msg = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=sys_prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Données du jour (JSON) :\n"
                        + json.dumps(payload, ensure_ascii=False, default=str)
                    ),
                },
            ],
        )
        text = "".join(
            getattr(b, "text", "") for b in msg.content
            if getattr(b, "type", None) == "text"
        ).strip()
        # Tolère un préfixe markdown ```json ... ```
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        data = json.loads(text)
        headline = str(data.get("headline", "")).strip()
        bullets = [str(b).strip() for b in data.get("bullets", []) if b]
        if not headline or not bullets:
            raise ValueError("Réponse LLM vide ou invalide")
        return headline, bullets

    # ==================================================================
    # SNAPSHOT QUOTIDIEN
    # ==================================================================
    async def snapshot_daily_kpis(
        self,
        snapshot_date: date | None = None,
    ) -> SnapshotRunResponse:
        """Écrit un snapshot des KPI nationaux pour la date donnée.

        Idempotent : si un snapshot existe déjà pour la date, on supprime
        l'ancien avant d'insérer le nouveau (le caller peut donc rejouer
        la tâche sans dupliquer la donnée).
        """
        snapshot_date = snapshot_date or _today_utc()

        # Recalcule des KPI bruts (sans cache, sinon on snapshote du stale).
        await self._cache_invalidate_national()
        kpis = await self.get_national_kpis()

        # Purge l'ancien snapshot du jour.
        await self.session.execute(
            delete(CockpitKpiSnapshot).where(
                and_(
                    CockpitKpiSnapshot.snapshotDate == snapshot_date,
                    CockpitKpiSnapshot.scope == CockpitScope.NATIONAL,
                )
            )
        )

        items_map = {
            KpiKey.STUDENTS_TOTAL: float(kpis.studentsTotal),
            KpiKey.ATTENDANCE_RATE: float(kpis.attendanceRate),
            KpiKey.BUDGET_CONSUMPTION: float(kpis.budgetConsumption),
            KpiKey.CRITICAL_ANOMALIES_OPEN: float(kpis.criticalAnomaliesOpen),
            KpiKey.ALERTS_OPEN: float(kpis.alertsOpen),
            # Module 1B — snapshot du GPI national (0.0 si non calculé).
            KpiKey.NATIONAL_GPI: (
                float(kpis.nationalGpi) if kpis.nationalGpi is not None
                else 0.0
            ),
            # Module 2C — snapshot count écoles CRITICAL t+1.
            KpiKey.PROJECTED_CRITICAL_SCHOOLS_COUNT: float(
                kpis.projectedCriticalSchools,
            ),
        }
        for key, value in items_map.items():
            row = CockpitKpiSnapshot(
                snapshotDate=snapshot_date,
                kpiKey=key,
                scope=CockpitScope.NATIONAL,
                value=value,
                extra={"generatedAt": kpis.generatedAt.isoformat()},
            )
            self.session.add(row)
        await self.session.flush()

        return SnapshotRunResponse(
            snapshotDate=snapshot_date,
            persisted=len(items_map),
            keys=[k.value for k in items_map],
            extra={},
        )

    # ==================================================================
    # COMPARISON J / J-1
    # ==================================================================
    async def compare_with_yesterday(self, kpi_key: KpiKey) -> ComparisonResponse:
        """Variation en pourcentage entre le snapshot d'aujourd'hui et hier.

        Si aucun snapshot n'existe pour aujourd'hui, on calcule la valeur
        live ; si aucun snapshot n'existe pour hier, ``yesterday=0`` et
        ``deltaPercent=0`` (évite la division par zéro).
        """
        today = _today_utc()
        yesterday = today - timedelta(days=1)

        rows_stmt = (
            select(CockpitKpiSnapshot)
            .where(
                and_(
                    CockpitKpiSnapshot.kpiKey == kpi_key,
                    CockpitKpiSnapshot.scope == CockpitScope.NATIONAL,
                    CockpitKpiSnapshot.snapshotDate.in_([today, yesterday]),
                )
            )
            .order_by(desc(CockpitKpiSnapshot.snapshotDate))
        )
        snapshots = list((await self.session.execute(rows_stmt)).scalars())

        snapshot_by_date: dict[date, CockpitKpiSnapshot] = {
            s.snapshotDate: s for s in snapshots
        }
        today_snapshot = snapshot_by_date.get(today)
        yesterday_snapshot = snapshot_by_date.get(yesterday)

        if today_snapshot is not None:
            today_value = float(today_snapshot.value)
        else:
            # Fallback live : on recalcule l'item.
            live = await self.get_national_kpis()
            today_value = float(live.items.get(kpi_key.value, 0.0))

        yesterday_value = (
            float(yesterday_snapshot.value)
            if yesterday_snapshot is not None else 0.0
        )

        delta = today_value - yesterday_value
        if yesterday_value:
            delta_percent = round((delta / yesterday_value) * 100.0, 2)
        else:
            delta_percent = 0.0 if delta == 0 else 100.0

        if abs(delta) < 0.001:
            direction = "stable"
        elif delta > 0:
            direction = "up"
        else:
            direction = "down"

        return ComparisonResponse(
            kpiKey=kpi_key,
            today=round(today_value, 2),
            yesterday=round(yesterday_value, 2),
            delta=round(delta, 2),
            deltaPercent=delta_percent,
            direction=direction,
            generatedAt=_now_utc(),
        )

    # ==================================================================
    # Helpers internes — agrégations isolées (gather-friendly)
    # ==================================================================
    @staticmethod
    def _coerce_date(value: Any) -> date:
        """Normalise un résultat ``func.date(...)`` en ``date``.

        Postgres renvoie un ``date`` natif via asyncpg, mais SQLite (qui
        peut être utilisé en backup CI) renvoie un ``str``. On gère les
        deux pour rester portable.
        """
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            return date.fromisoformat(value[:10])
        # Fallback : essai best-effort via str()
        return date.fromisoformat(str(value)[:10])

    async def _count_students(self) -> int:
        try:
            stmt = select(func.count(Student.id))
            return int((await self.session.execute(stmt)).scalar_one() or 0)
        except Exception as exc:
            logger.warning("cockpit._count_students failed: {}", exc)
            return 0

    async def _compute_attendance_rate_recent(self) -> float:
        """Taux de présence pays sur les 7 derniers jours."""
        try:
            cutoff = _now_utc() - timedelta(days=7)
            total_stmt = (
                select(func.count(AttendanceRecord.id))
                .where(AttendanceRecord.scannedAt >= cutoff)
            )
            present_stmt = (
                select(func.count(AttendanceRecord.id))
                .where(
                    and_(
                        AttendanceRecord.scannedAt >= cutoff,
                        AttendanceRecord.status == AttendanceStatus.PRESENT,
                    )
                )
            )
            total, present = await asyncio.gather(
                self.session.execute(total_stmt),
                self.session.execute(present_stmt),
            )
            total_v = int(total.scalar_one() or 0)
            present_v = int(present.scalar_one() or 0)
            if not total_v:
                return 0.0
            return (present_v / total_v) * 100.0
        except Exception as exc:
            logger.warning(
                "cockpit._compute_attendance_rate_recent failed: {}", exc,
            )
            return 0.0

    async def _compute_budget_consumption(self) -> float:
        """Pourcentage de budget consommé (sum(expenses)/sum(budgets))."""
        try:
            planned_stmt = select(func.coalesce(func.sum(Budget.amountPlanned), 0.0))
            spent_stmt = select(func.coalesce(func.sum(Expense.amount), 0.0))
            planned, spent = await asyncio.gather(
                self.session.execute(planned_stmt),
                self.session.execute(spent_stmt),
            )
            planned_v = float(planned.scalar_one() or 0.0)
            spent_v = float(spent.scalar_one() or 0.0)
            if not planned_v:
                return 0.0
            return (spent_v / planned_v) * 100.0
        except Exception as exc:
            logger.warning(
                "cockpit._compute_budget_consumption failed: {}", exc,
            )
            return 0.0

    async def _count_critical_open_anomalies(self) -> int:
        try:
            stmt = (
                select(func.count(AnomalyDetection.id))
                .where(
                    and_(
                        AnomalyDetection.severity == AnomalySeverity.CRITICAL,
                        AnomalyDetection.status == AnomalyStatus.PENDING,
                    )
                )
            )
            return int((await self.session.execute(stmt)).scalar_one() or 0)
        except Exception as exc:
            logger.warning(
                "cockpit._count_critical_open_anomalies failed: {}", exc,
            )
            return 0

    async def _count_alerts_open(self) -> int:
        """Alertes ouvertes = anomalies PENDING (toutes sévérités)."""
        try:
            stmt = (
                select(func.count(AnomalyDetection.id))
                .where(AnomalyDetection.status == AnomalyStatus.PENDING)
            )
            return int((await self.session.execute(stmt)).scalar_one() or 0)
        except Exception as exc:
            logger.warning("cockpit._count_alerts_open failed: {}", exc)
            return 0

    async def _count_projected_critical_schools(self) -> int:
        """Module 2C — Compte écoles CRITICAL sur projection +1 an.

        Pour rester déterministe, on cible l'horizon +1 an de la
        projection la plus récente (max(projectedYear) où il existe au
        moins un snapshot scope=SCHOOL × severity=CRITICAL). Retourne 0
        si aucun snapshot n'a encore été calculé.
        """
        try:
            from app.modules.projections.enums import (
                CapacityScope,
                CapacitySeverity,
            )
            from app.modules.projections.models import (
                CapacityDemandSnapshot,
            )

            # On prend l'année projetée min (la plus proche du présent)
            # qui possède au moins un snapshot SCHOOL — cible +1 an
            # par construction du recalcul Module 2C.
            min_year_stmt = (
                select(func.min(CapacityDemandSnapshot.projectedYear))
                .where(
                    CapacityDemandSnapshot.scope == CapacityScope.SCHOOL,
                )
            )
            min_year = (
                await self.session.execute(min_year_stmt)
            ).scalar_one_or_none()
            if min_year is None:
                return 0

            stmt = (
                select(func.count(CapacityDemandSnapshot.id))
                .where(
                    CapacityDemandSnapshot.scope == CapacityScope.SCHOOL,
                    CapacityDemandSnapshot.severity
                    == CapacitySeverity.CRITICAL,
                    CapacityDemandSnapshot.projectedYear == min_year,
                )
            )
            return int((await self.session.execute(stmt)).scalar_one() or 0)
        except Exception as exc:
            logger.warning(
                "cockpit._count_projected_critical_schools failed: {}", exc,
            )
            return 0

    async def _compute_national_gpi(self) -> Any:
        """GPI national courant (Module 1B).

        Lit le dernier snapshot ``GpiSnapshot(scope=NATIONAL, entityId=NULL)``.
        Retourne ``None`` si aucun snapshot n'a été calculé (équipe ops doit
        lancer ``compute_gpi_snapshots`` au moins une fois).

        Renvoyé en ``Decimal`` (pas float) pour préserver la précision —
        Pydantic le sérialise correctement côté API.
        """
        try:
            from app.modules.enrollment.enums import GpiScope as _GpiScope
            from app.modules.enrollment.models import GpiSnapshot

            stmt = (
                select(GpiSnapshot.gpi)
                .where(
                    GpiSnapshot.scope == _GpiScope.NATIONAL,
                    GpiSnapshot.entityId.is_(None),
                )
                .order_by(GpiSnapshot.computedAt.desc())
                .limit(1)
            )
            value = (await self.session.execute(stmt)).scalar_one_or_none()
            return value
        except Exception as exc:
            logger.warning("cockpit._compute_national_gpi failed: {}", exc)
            return None

    # ==================================================================
    # Module 1C — Urban / Rural gap
    # ==================================================================
    async def get_urban_rural_gap(
        self,
        school_year_id: str,
    ) -> UrbanRuralGapResponse:
        """KPI Module 1C : écart de GPI entre zones urbaine et rurale.

        On agrège ``Enrollment`` filtré sur ``CENSUS_DECLARED`` × ``school_year_id``
        et on calcule par zone effective (``COALESCE(School.zoneType,
        SubPrefecture.defaultZoneType)``). Cache Redis 30s.
        """
        cache_key = f"urban_rural_gap:{school_year_id}"
        cached = await self._cache_get(cache_key)
        if cached is not None:
            cached["cached"] = True
            return UrbanRuralGapResponse(**cached)

        from decimal import Decimal as _Decimal

        from app.modules.enrollment.enums import EnrollmentSource
        from app.modules.enrollment.models import Enrollment
        from app.modules.territory.models import SubPrefecture as _SubPref
        from app.shared.enums import Gender as _Gender
        from app.shared.enums import ZoneType as _ZoneType

        effective_zone = func.coalesce(
            School.zoneType, _SubPref.defaultZoneType,
        ).label("effective_zone")

        stmt = (
            select(
                effective_zone,
                Enrollment.gender,
                func.coalesce(func.sum(Enrollment.count), 0).label("total"),
            )
            .select_from(Enrollment)
            .join(School, School.id == Enrollment.schoolId)
            .outerjoin(_SubPref, _SubPref.id == School.subPrefectureId)
            .where(
                and_(
                    Enrollment.schoolYearId == school_year_id,
                    Enrollment.source == EnrollmentSource.CENSUS_DECLARED,
                )
            )
            .group_by(effective_zone, Enrollment.gender)
        )
        rows = (await self.session.execute(stmt)).all()

        # Aggregate par zone -> {gender: count}
        by_zone: dict[_ZoneType, dict[_Gender, int]] = {
            _ZoneType.URBAN: {_Gender.FEMALE: 0, _Gender.MALE: 0},
            _ZoneType.RURAL: {_Gender.FEMALE: 0, _Gender.MALE: 0},
            _ZoneType.PERI_URBAN: {_Gender.FEMALE: 0, _Gender.MALE: 0},
        }
        for zone_raw, gender, total in rows:
            zone = (
                _ZoneType(zone_raw)
                if zone_raw is not None
                else _ZoneType.RURAL
            )
            if gender in (_Gender.FEMALE, _Gender.MALE):
                by_zone[zone][gender] += int(total)

        def _gpi(g: int, b: int) -> _Decimal | None:
            if b <= 0:
                return None
            return (_Decimal(g) / _Decimal(b)).quantize(_Decimal("0.0001"))

        urban_g = by_zone[_ZoneType.URBAN][_Gender.FEMALE]
        urban_b = by_zone[_ZoneType.URBAN][_Gender.MALE]
        rural_g = by_zone[_ZoneType.RURAL][_Gender.FEMALE]
        rural_b = by_zone[_ZoneType.RURAL][_Gender.MALE]
        peri_g = by_zone[_ZoneType.PERI_URBAN][_Gender.FEMALE]
        peri_b = by_zone[_ZoneType.PERI_URBAN][_Gender.MALE]

        urban_gpi = _gpi(urban_g, urban_b)
        rural_gpi = _gpi(rural_g, rural_b)
        peri_gpi = _gpi(peri_g, peri_b)

        if urban_gpi is not None and rural_gpi is not None:
            delta = abs(urban_gpi - rural_gpi)
        else:
            delta = None

        response = UrbanRuralGapResponse(
            schoolYearId=school_year_id,
            urbanGpi=urban_gpi,
            ruralGpi=rural_gpi,
            periUrbanGpi=peri_gpi,
            deltaGpi=delta,
            urbanGirlsCount=urban_g,
            urbanBoysCount=urban_b,
            ruralGirlsCount=rural_g,
            ruralBoysCount=rural_b,
            periUrbanGirlsCount=peri_g,
            periUrbanBoysCount=peri_b,
            urbanCount=urban_g + urban_b,
            ruralCount=rural_g + rural_b,
            periUrbanCount=peri_g + peri_b,
            generatedAt=_now_utc(),
            cached=False,
        )
        await self._cache_set(cache_key, response.model_dump(mode="json"))
        return response

    # ==================================================================
    # Redis cache helpers
    # ==================================================================
    async def _cache_get(self, suffix: str) -> dict[str, Any] | None:
        try:
            redis = get_redis()
        except Exception:  # pragma: no cover - redis disabled
            return None
        try:
            raw = await redis.get(_cache_key(suffix))
        except Exception:
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def _cache_set(
        self, suffix: str, payload: dict[str, Any],
    ) -> None:
        try:
            redis = get_redis()
        except Exception:  # pragma: no cover - redis disabled
            return
        try:
            await redis.setex(
                _cache_key(suffix),
                CACHE_TTL_SECONDS,
                json.dumps(payload, ensure_ascii=False, default=str),
            )
        except Exception:
            return

    async def _cache_invalidate_national(self) -> None:
        try:
            redis = get_redis()
            await redis.delete(_cache_key("kpis:national"))
        except Exception:  # pragma: no cover - redis disabled
            return


# Helpers stand-alone (utile pour usage hors instance, e.g. côté workers).
async def publish_cockpit_alert(
    *,
    school_id: str | None,
    region_id: str | None,
    severity: str,
    summary: str,
) -> None:
    """Publie un évènement temps réel sur le canal cockpit:alert.

    Réutilise la couche RealtimeService (Module 13). Best-effort : on log
    et on continue si la couche Redis tombe.
    """
    try:
        from app.modules.realtime.events import (
            CHANNEL_PREFIX,
            Event,
            EventType,
            publish,
        )

        redis = get_redis()
        ev = Event(
            type=EventType.ANOMALY_DETECTED,
            payload={
                "channel": "cockpit:alert",
                "severity": severity,
                "summary": summary,
            },
            schoolId=school_id,
            regionId=region_id,
        )
        # Publie aussi sur un channel dédié cockpit pour les abonnés cabinet.
        await redis.publish(
            f"{CHANNEL_PREFIX}:cockpit:alert", ev.model_dump_json(),
        )
        await publish(redis, ev)
    except Exception as exc:  # pragma: no cover - redis offline
        logger.warning("cockpit.publish_alert failed: {}", exc)


# Tri-friendly export
def aggregate_severities(severities: list[str]) -> dict[str, int]:
    """Utilitaire : groupe par sévérité pour la timeline (utilisé par tests)."""
    out: dict[str, int] = defaultdict(int)
    for s in severities:
        out[s] += 1
    return dict(out)


__all__ = [
    "CACHE_TTL_SECONDS",
    "CockpitService",
    "aggregate_severities",
    "publish_cockpit_alert",
]
