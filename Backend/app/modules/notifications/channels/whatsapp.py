"""WhatsApp adapter — Meta Cloud API (Graph) v21.

Sends a free-form text message to a verified business number. For templates
(needed outside the 24h conversation window) we fall back to a plain text
payload — template orchestration is left for Phase 8 when templates are
declared in the Meta dashboard.
"""
from __future__ import annotations

import httpx
from loguru import logger

from app.core.config import settings
from app.modules.notifications.channels.base import (
    ChannelAdapter,
    ChannelMessage,
    ChannelResult,
    normalize_phone,
)


class WhatsappCloudAdapter(ChannelAdapter):
    name = "whatsapp"
    _BASE_URL = "https://graph.facebook.com/v21.0"

    def is_configured(self) -> bool:
        return bool(settings.whatsapp_api_token and settings.whatsapp_phone_id)

    async def send(self, msg: ChannelMessage) -> ChannelResult:
        if not self.is_configured():
            logger.warning("WhatsApp adapter not configured — skipping send")
            return ChannelResult(ok=False, error="not_configured")

        to = normalize_phone(msg.recipient).lstrip("+")
        url = f"{self._BASE_URL}/{settings.whatsapp_phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {settings.whatsapp_api_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": msg.message, "preview_url": False},
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            return ChannelResult(ok=False, error=f"http_error:{exc.__class__.__name__}")

        if response.status_code >= 400:
            return ChannelResult(
                ok=False,
                error=f"whatsapp_{response.status_code}:{response.text[:200]}",
            )
        body = response.json()
        # Cloud API returns {messages: [{id: "wamid..."}]}
        provider_id = None
        messages = body.get("messages") or []
        if messages and isinstance(messages, list):
            provider_id = messages[0].get("id")
        return ChannelResult(ok=True, provider_id=provider_id)
