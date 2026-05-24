"""Module 13 — Realtime event model + Redis Pub/Sub I/O.

Architecture
------------
* `EventType` : enum strict des évènements diffusés au cockpit ministériel
  (scan présence, incident, anomalie, prédiction décrochage, bulletin).
* `Event` : payload Pydantic versionné. Sérialisé en JSON sur Redis Pub/Sub.
* `publish()` / `subscribe()` : helpers minces autour de `redis.asyncio.Redis`
  qui isolent l'application du choix transport (Redis Pub/Sub) — si demain
  on bascule sur Kafka / NATS / Centrifugo, seule cette couche change.

Channel naming
--------------
`gestionee:events:<scope>` où `<scope>` ∈ {`global`, `region:<id>`, `school:<id>`}.

* Les évènements *à diffusion nationale* (anomalies critiques de portée
  ministérielle, bulletins générés en masse, etc.) vont sur `global`.
* Les évènements scope-régional vont sur `region:<id>` ; un REGIONAL_ADMIN
  s'y abonne nominativement.
* Les évènements scope-école vont sur `school:<id>` ; un SCHOOL_DIRECTOR
  reçoit son école (et la région parente pour les annonces).

Le filtrage est donc fait *côté serveur via la sélection des channels* —
on évite ainsi tout fan-out massif (un REGIONAL_ADMIN ne reçoit jamais
le flot d'une autre région).
"""
from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

CHANNEL_PREFIX = "gestionee:events"
GLOBAL_CHANNEL = f"{CHANNEL_PREFIX}:global"


class EventType(StrEnum):
    """Types d'évènements broadcastés. STRICT — un client connecté ne doit
    jamais recevoir un type qu'il ne sait pas désérialiser.
    """

    ATTENDANCE_SCAN = "ATTENDANCE_SCAN"
    INCIDENT_CREATED = "INCIDENT_CREATED"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    DROPOUT_PREDICTION_HIGH = "DROPOUT_PREDICTION_HIGH"
    BULLETIN_GENERATED = "BULLETIN_GENERATED"


class Event(BaseModel):
    """Évènement diffusé sur le bus temps réel.

    `schoolId` / `regionId` sont optionnels : un event sans scope sera
    publié sur `global` ; un event avec `schoolId` ira sur le channel
    school + region (cumulé) pour qu'un directeur ET son régional reçoivent.
    """

    model_config = ConfigDict(use_enum_values=True)

    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    schoolId: str | None = None
    regionId: str | None = None
    occurredAt: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def channels(self) -> list[str]:
        """Channels Redis sur lesquels publier cet event.

        Politique :
        * `schoolId` présent → on publie sur `school:<id>` ET `region:<regionId>`
          (si la région est connue) pour que la chaîne hiérarchique reçoive.
        * Sinon, `regionId` présent → on publie uniquement sur `region:<id>`.
        * Sinon → on publie sur `global`.

        Le rationale est qu'un REGIONAL_ADMIN ne s'abonne pas aux channels
        écoles individuels (trop nombreux) ; il s'abonne au channel région
        qui agrège tout ce qui se passe dans son périmètre.
        """
        if self.schoolId is not None:
            channels = [f"{CHANNEL_PREFIX}:school:{self.schoolId}"]
            if self.regionId is not None:
                channels.append(f"{CHANNEL_PREFIX}:region:{self.regionId}")
            channels.append(GLOBAL_CHANNEL)
            return channels
        if self.regionId is not None:
            return [f"{CHANNEL_PREFIX}:region:{self.regionId}", GLOBAL_CHANNEL]
        return [GLOBAL_CHANNEL]


# ---------------------------------------------------------------------------
# Publish / Subscribe
# ---------------------------------------------------------------------------
async def publish(redis: Redis, event: Event) -> int:
    """Publie l'évènement sur tous les channels concernés.

    Retourne la somme du nombre d'abonnés notifiés sur chaque channel
    (info utile pour métrique / debug ; pas critique).

    Tolère les pannes Redis : log et retourne 0 plutôt que de casser la
    transaction métier en cours (le temps réel est best-effort).
    """
    blob = event.model_dump_json()
    total = 0
    try:
        for channel in event.channels():
            total += int(await redis.publish(channel, blob) or 0)
    except Exception:  # pragma: no cover — Redis transient
        # NB: on ne re-raise pas. Le service métier ne doit pas planter
        # parce que Redis a hoqueté pendant la publication d'un event.
        import logging

        logging.getLogger(__name__).warning(
            "realtime.publish failed for %s — event dropped", event.type
        )
        return 0
    return total


async def subscribe(redis: Redis, channels: list[str]) -> AsyncIterator[Event]:
    """Abonne le client `redis` aux channels et yield les events parsés.

    Note : c'est un async generator. Le caller (WebSocket handler) doit
    boucler `async for event in subscribe(...)` et gérer la fermeture
    (cancel de la tâche → cleanup PubSub).
    """
    if not channels:
        return
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(*channels)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if data is None:
                continue
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            try:
                payload = json.loads(data)
                yield Event.model_validate(payload)
            except (json.JSONDecodeError, ValueError):
                # Message malformé : on log et on continue.
                import logging

                logging.getLogger(__name__).warning(
                    "realtime.subscribe: dropping malformed message"
                )
                continue
    finally:
        with contextlib.suppress(Exception):  # pragma: no cover — best-effort cleanup
            await pubsub.unsubscribe(*channels)
        with contextlib.suppress(Exception):  # pragma: no cover — best-effort cleanup
            await pubsub.aclose()


__all__ = [
    "CHANNEL_PREFIX",
    "GLOBAL_CHANNEL",
    "Event",
    "EventType",
    "publish",
    "subscribe",
]
