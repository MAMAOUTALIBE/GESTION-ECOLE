"""Module 13 — Realtime WebSocket router.

Deux endpoints sont exposés :

* ``WS /notifications`` (legacy phase 14) : notifications par userId — gardé
  pour compatibilité ascendante avec le code existant (``app.modules.sms``
  importe ``notify_user``).
* ``WS /connect`` (Module 13) : flux d'évènements typés Redis Pub/Sub
  authentifié via JWT en query param. C'est le pipe que consomme le cockpit
  ministériel Angular.

Le second pipe diffuse 5 types d'évènements : ATTENDANCE_SCAN, INCIDENT_CREATED,
ANOMALY_DETECTED, DROPOUT_PREDICTION_HIGH, BULLETIN_GENERATED. Le scope
territorial est appliqué SERVEUR-SIDE via la sélection des channels Redis :
un REGIONAL_ADMIN ne s'abonne qu'à `region:<id>` + `global` — il ne reçoit
jamais le flot d'une autre région.
"""
import asyncio
import contextlib
import json
import logging
from collections import defaultdict
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.redis import get_redis
from app.core.security import decode_token
from app.modules.realtime.events import subscribe
from app.modules.realtime.scope_channels import channels_for_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["realtime"])

# Heartbeat — intervalle d'envoi du ping serveur pour détecter les sockets
# zombies. Le client doit répondre par PONG ou la connexion sera fermée
# côté infra (proxy, load balancer) après ~60s sans trafic.
HEARTBEAT_INTERVAL_SECONDS = 30


# =========================================================================
# Legacy phase 14 — notifications par userId (gardé pour app.modules.sms)
# =========================================================================
class ConnectionManager:
    """Gère les WebSockets actives par user_id."""

    def __init__(self) -> None:
        self.active: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, ws: WebSocket, user_id: str) -> None:
        await ws.accept()
        self.active[user_id].add(ws)
        logger.info("WS connected user=%s total=%s", user_id, len(self.active[user_id]))

    def disconnect(self, ws: WebSocket, user_id: str) -> None:
        self.active[user_id].discard(ws)
        if not self.active[user_id]:
            del self.active[user_id]

    async def push_to_user(self, user_id: str, payload: dict[str, Any]) -> None:
        """Envoie un message à toutes les sessions d'un user."""
        sockets = list(self.active.get(user_id, []))
        if not sockets:
            return
        message = json.dumps(payload, default=str)
        results = await asyncio.gather(
            *(ws.send_text(message) for ws in sockets),
            return_exceptions=True,
        )
        # Nettoie les sockets fermées
        for ws, res in zip(sockets, results, strict=True):
            if isinstance(res, Exception):
                self.disconnect(ws, user_id)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        """Envoie à tous les users connectés (pour alertes système)."""
        for user_id in list(self.active.keys()):
            await self.push_to_user(user_id, payload)


manager = ConnectionManager()


@router.websocket("/notifications")
async def notifications_ws(ws: WebSocket, userId: str = Query(...)) -> None:
    """Handshake : passer ?userId=... dans l'URL.

    En production, valider via un JWT en query param ou cookie.
    """
    await manager.connect(ws, userId)
    # Greeting initial pour confirmer
    await ws.send_text(json.dumps({
        "type": "WELCOME",
        "userId": userId,
        "message": "Connecté aux notifications temps réel.",
    }))
    try:
        while True:
            # On reste à l'écoute (heartbeat possible côté client)
            data = await ws.receive_text()
            # Echo simple — le client peut envoyer "ping" pour keep-alive
            if data == "ping":
                await ws.send_text(json.dumps({"type": "PONG"}))
    except WebSocketDisconnect:
        manager.disconnect(ws, userId)


# Helper exposé aux autres modules pour pousser une notification
async def notify_user(user_id: str, payload: dict[str, Any]) -> None:
    await manager.push_to_user(user_id, payload)


async def broadcast_alert(payload: dict[str, Any]) -> None:
    await manager.broadcast(payload)


# =========================================================================
# Module 13 — /connect : pipe authentifié JWT, scope-aware, Redis Pub/Sub
# =========================================================================
async def _resolve_user_from_token(
    token: str, session: AsyncSession
) -> Any | None:
    """Décode le JWT (type=access), lookup le User, applique les contrôles
    minimum (isActive). Retourne ``None`` si rejet — le caller fermera le WS.
    """
    try:
        payload = decode_token(token, expected_type="access")
    except jwt.PyJWTError:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    from app.modules.auth.models import User

    user = await session.get(User, user_id)
    if user is None or not user.isActive:
        return None
    return user


async def _heartbeat_loop(ws: WebSocket) -> None:
    """Tâche concurrente qui envoie un ping toutes les N secondes.

    Si le client ne répond pas (ou la socket est morte), `send_text` lèvera
    une `WebSocketDisconnect` qui annule le subscribe en parallèle.
    """
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        await ws.send_text(json.dumps({"type": "PING"}))


async def _forward_events(ws: WebSocket, channels: list[str]) -> None:
    """Boucle de forward : subscribe Redis → send_json(event)."""
    redis = get_redis()
    async for event in subscribe(redis, channels):
        # `model_dump(mode='json')` sérialise occurredAt en ISO 8601 et l'enum
        # via sa valeur string.
        await ws.send_json(event.model_dump(mode="json"))


@router.websocket("/connect")
async def connect_ws(
    ws: WebSocket,
    session: Annotated[AsyncSession, Depends(get_session)],
    token: str = Query(..., description="JWT access token"),
) -> None:
    """Endpoint principal Module 13 — flux temps réel cockpit.

    Authentification : ``?token=<jwt>`` (limitation de la spec WebSocket :
    pas de header Authorization standard côté navigateur).

    Flow :
    1. Décode + valide le JWT, charge le User en DB.
    2. Calcule les channels Redis à subscribe via ``channels_for_user``.
    3. Boucle ``async for event in subscribe()`` → ``send_json``.
    4. Tâche heartbeat en parallèle (envoie PING toutes les 30s).
    5. Fermeture gracieuse sur déconnexion / token expiré.

    Codes de fermeture (RFC 6455) :
    * 1008 (policy violation) : JWT invalide / révoqué.
    * 1000 (normal closure) : déconnexion propre.
    """
    # Étape 1 — auth. On accepte le handshake AVANT validation (sinon le
    # client ne reçoit pas le close code) puis on close immédiatement si KO.
    await ws.accept()

    # Session DB injectée par FastAPI Depends — visible aux tests via
    # dependency_overrides[get_session].
    user = await _resolve_user_from_token(token, session)

    if user is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
        return

    # Étape 2 — channels
    channels = channels_for_user(user)

    # Étape 3 — greeting
    await ws.send_json(
        {
            "type": "WELCOME",
            "userId": user.id,
            "role": user.role.value if user.role else None,
            "channels": channels,
        }
    )

    # Étape 4 — boucle forward + heartbeat en parallèle. Le premier des
    # deux qui lève (déconnexion ou erreur) cancel l'autre.
    forward_task = asyncio.create_task(_forward_events(ws, channels))
    heartbeat_task = asyncio.create_task(_heartbeat_loop(ws))

    done: set[asyncio.Task[Any]] = set()
    pending: set[asyncio.Task[Any]] = set()
    try:
        done, pending = await asyncio.wait(
            {forward_task, heartbeat_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
    except WebSocketDisconnect:
        pass
    finally:
        for task in (forward_task, heartbeat_task):
            if not task.done():
                task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, Exception):
                await task
        # Si une des tâches a fini en erreur autre que WebSocketDisconnect, log.
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                logger.warning("realtime.connect: task ended with %s", exc)
        # Best-effort close — peut déjà être fermé.
        with contextlib.suppress(Exception):  # pragma: no cover
            await ws.close()
