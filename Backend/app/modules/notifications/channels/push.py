"""Push notifications via Firebase Cloud Messaging (legacy HTTP API).

Uses the legacy server key (Authorization: key=...) for simplicity. Migrating
to FCM HTTP v1 (OAuth2 service account) is straightforward when needed —
swap the auth header and switch to the v1 endpoint.

Recipient field carries an FCM device token.
"""
from __future__ import annotations

import httpx
from loguru import logger

from app.core.config import settings
from app.modules.notifications.channels.base import (
    ChannelAdapter,
    ChannelMessage,
    ChannelResult,
)


class FcmPushAdapter(ChannelAdapter):
    name = "push"
    _URL = "https://fcm.googleapis.com/fcm/send"

    def is_configured(self) -> bool:
        return bool(settings.fcm_server_key)

    async def send(self, msg: ChannelMessage) -> ChannelResult:
        if not self.is_configured():
            logger.warning("Push adapter not configured — skipping send")
            return ChannelResult(ok=False, error="not_configured")

        headers = {
            "Authorization": f"key={settings.fcm_server_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, object] = {
            "to": msg.recipient,
            "notification": {
                "title": msg.subject or "GESTION-EE",
                "body": msg.message,
            },
        }
        if msg.metadata:
            payload["data"] = dict(msg.metadata)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self._URL, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            return ChannelResult(ok=False, error=f"http_error:{exc.__class__.__name__}")

        if response.status_code >= 400:
            return ChannelResult(
                ok=False, error=f"fcm_{response.status_code}:{response.text[:200]}"
            )
        body = response.json()
        if body.get("failure", 0) > 0 or body.get("success", 0) == 0:
            return ChannelResult(
                ok=False,
                error=f"fcm_unsuccessful:{body.get('results', [])[:1]}",
            )
        provider_id = None
        results = body.get("results") or []
        if results and isinstance(results, list):
            provider_id = results[0].get("message_id")
        return ChannelResult(ok=True, provider_id=provider_id)
