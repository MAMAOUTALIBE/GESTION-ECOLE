"""Phase 14 — Notifications temps réel via WebSocket.

Architecture simple en mémoire (suffisant pour 1 process FastAPI ; en cluster
multi-worker il faudra Redis pubsub).
"""
import asyncio
import json
import logging
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter(tags=["realtime"])


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
