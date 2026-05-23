"""SMS adapter — Twilio REST (primary) with Orange API hook reserved for later.

The HTTP call uses ``httpx.AsyncClient`` with a 10s timeout. Errors are caught
and surfaced as ``ChannelResult(ok=False, error=...)`` — never raised.
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


class TwilioSmsAdapter(ChannelAdapter):
    name = "sms"
    _BASE_URL = "https://api.twilio.com/2010-04-01"

    def is_configured(self) -> bool:
        return bool(
            settings.twilio_account_sid
            and settings.twilio_auth_token
            and settings.twilio_from_number
        )

    async def send(self, msg: ChannelMessage) -> ChannelResult:
        if not self.is_configured():
            logger.warning("SMS adapter not configured — skipping send")
            return ChannelResult(ok=False, error="not_configured")

        to = normalize_phone(msg.recipient)
        url = f"{self._BASE_URL}/Accounts/{settings.twilio_account_sid}/Messages.json"
        data = {
            "To": to,
            "From": settings.twilio_from_number,
            "Body": msg.message,
        }
        auth = (settings.twilio_account_sid or "", settings.twilio_auth_token or "")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, data=data, auth=auth)
        except httpx.HTTPError as exc:
            return ChannelResult(ok=False, error=f"http_error:{exc.__class__.__name__}")

        if response.status_code >= 400:
            return ChannelResult(
                ok=False, error=f"twilio_{response.status_code}:{response.text[:200]}"
            )
        body = response.json()
        return ChannelResult(ok=True, provider_id=body.get("sid"))
