"""Module 13 — RealtimeService : façade publish pour les services métier.

Coupling fort évité : les services métier (attendance, schoollife, anomalies,
predictions, reports) appellent UN seul helper d'une seule ligne, par exemple
``await RealtimeService.publish_attendance_scan(school_id, region_id, n)``.

Pannes Redis tolérées : le helper logue et retourne 0 (cf. ``events.publish``).
Le service métier ne plante JAMAIS à cause de la couche temps réel.

Resolveurs region (region_id) : le service métier doit fournir le `regionId`
quand il le connaît (souvent dispo via `school.regionId` chargé en eager).
Si ce n'est pas le cas, on accepte ``None`` et l'event sera publié sur
`global` + `school:<id>` uniquement.
"""
from __future__ import annotations

from typing import Any

from app.core.redis import get_redis
from app.modules.realtime.events import Event, EventType, publish


class RealtimeService:
    """Stateless façade. Toutes les méthodes sont des classmethod (pas
    d'état d'instance) — on n'a même pas besoin d'instancier la classe.

    On la garde quand même sous forme de class pour pouvoir la mocker
    facilement en test : `mocker.patch.object(RealtimeService, 'publish_...')`.
    """

    @classmethod
    async def _publish(
        cls,
        event_type: EventType,
        payload: dict[str, Any],
        *,
        school_id: str | None = None,
        region_id: str | None = None,
    ) -> int:
        """Publie un event en utilisant le Redis singleton de l'app."""
        event = Event(
            type=event_type,
            payload=payload,
            schoolId=school_id,
            regionId=region_id,
        )
        try:
            redis = get_redis()
        except Exception:  # pragma: no cover — Redis init failed
            return 0
        return await publish(redis, event)

    # ------------------------------------------------------------------
    # Events publics — un wrapper typé par event metier.
    # ------------------------------------------------------------------
    @classmethod
    async def publish_attendance_scan(
        cls,
        school_id: str,
        region_id: str | None,
        count: int,
    ) -> int:
        """Publié à la fin d'un bulk_scan attendance."""
        return await cls._publish(
            EventType.ATTENDANCE_SCAN,
            {"schoolId": school_id, "count": count},
            school_id=school_id,
            region_id=region_id,
        )

    @classmethod
    async def publish_incident(
        cls,
        school_id: str,
        region_id: str | None,
        severity: str,
        incident_id: str | None = None,
    ) -> int:
        """Publié à chaque création d'incident discipline."""
        payload: dict[str, Any] = {
            "schoolId": school_id,
            "severity": severity,
        }
        if incident_id is not None:
            payload["incidentId"] = incident_id
        return await cls._publish(
            EventType.INCIDENT_CREATED,
            payload,
            school_id=school_id,
            region_id=region_id,
        )

    @classmethod
    async def publish_anomaly(
        cls,
        region_id: str | None,
        anomaly_type: str,
        severity: str,
        school_id: str | None = None,
        anomaly_id: str | None = None,
    ) -> int:
        """Publié pour chaque anomalie CRITICAL détectée.

        Module 19 — Si la sévérité est CRITICAL on publie en parallèle sur
        le canal dédié ``cockpit:alert`` (consommé par le cockpit
        ministériel cabinet). Best-effort : un échec sur le mirror
        cockpit ne casse pas le publish principal.
        """
        payload: dict[str, Any] = {
            "anomalyType": anomaly_type,
            "severity": severity,
        }
        if anomaly_id is not None:
            payload["anomalyId"] = anomaly_id
        if school_id is not None:
            payload["schoolId"] = school_id
        published = await cls._publish(
            EventType.ANOMALY_DETECTED,
            payload,
            school_id=school_id,
            region_id=region_id,
        )
        # Mirror sur le canal cockpit pour les CRITICAL uniquement (signal
        # ministériel ; évite que le cabinet reçoive du bruit MEDIUM/LOW).
        if str(severity).upper() == "CRITICAL":
            import contextlib

            with contextlib.suppress(Exception):  # pragma: no cover - best-effort
                await cls.publish_cockpit_alert(
                    severity=severity,
                    summary=f"{anomaly_type} (anomalyId={anomaly_id})",
                    school_id=school_id,
                    region_id=region_id,
                )
        return published

    @classmethod
    async def publish_cockpit_alert(
        cls,
        *,
        severity: str,
        summary: str,
        school_id: str | None = None,
        region_id: str | None = None,
    ) -> int:
        """Publie un évènement dédié au cockpit ministériel.

        Channel : ``gestionee:events:cockpit:alert`` — un abonné cabinet
        s'y abonne pour recevoir le flux temps réel des CRITICAL.
        """
        try:
            redis = get_redis()
        except Exception:  # pragma: no cover - redis init
            return 0
        from app.modules.realtime.events import CHANNEL_PREFIX, Event

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
        try:
            return int(
                await redis.publish(
                    f"{CHANNEL_PREFIX}:cockpit:alert",
                    ev.model_dump_json(),
                )
                or 0
            )
        except Exception:  # pragma: no cover - redis transient
            return 0

    @classmethod
    async def publish_dropout_prediction_high(
        cls,
        student_id: str,
        school_id: str,
        region_id: str | None,
        probability: float | None = None,
    ) -> int:
        """Publié quand une prédiction de décrochage atteint riskLevel=HIGH."""
        payload: dict[str, Any] = {
            "studentId": student_id,
            "schoolId": school_id,
        }
        if probability is not None:
            payload["probability"] = round(float(probability), 4)
        return await cls._publish(
            EventType.DROPOUT_PREDICTION_HIGH,
            payload,
            school_id=school_id,
            region_id=region_id,
        )

    @classmethod
    async def publish_bulletin_generated(
        cls,
        student_id: str,
        school_id: str,
        region_id: str | None,
        report_card_id: str,
    ) -> int:
        """Publié quand un bulletin PDF a fini d'être généré."""
        return await cls._publish(
            EventType.BULLETIN_GENERATED,
            {
                "studentId": student_id,
                "schoolId": school_id,
                "reportCardId": report_card_id,
            },
            school_id=school_id,
            region_id=region_id,
        )

    # ------------------------------------------------------------------
    # Stats légères pour le /stats endpoint (cockpit ops)
    # ------------------------------------------------------------------
    @classmethod
    async def get_stats(cls) -> dict[str, Any]:
        """Renvoie un snapshot opérationnel — nb total subscribers Pub/Sub.

        On utilise PUBSUB NUMSUB côté Redis : pas besoin de maintenir un
        compteur applicatif (source de vérité = Redis lui-même).
        """
        try:
            redis = get_redis()
            # PUBSUB NUMPAT (nb patterns) + nombre de channels actifs
            try:
                channels = await redis.pubsub_channels("gestionee:events:*")
            except Exception:
                channels = []
            numsub: dict[str, int] = {}
            if channels:
                try:
                    numsub = dict(await redis.pubsub_numsub(*channels))
                except Exception:
                    numsub = {}
            total = sum(int(v) for v in numsub.values())
            return {
                "activeChannels": len(channels),
                "totalSubscribers": total,
                "byChannel": numsub,
            }
        except Exception as exc:  # pragma: no cover - Redis down
            return {
                "activeChannels": 0,
                "totalSubscribers": 0,
                "byChannel": {},
                "error": str(exc),
            }


__all__ = ["RealtimeService"]
