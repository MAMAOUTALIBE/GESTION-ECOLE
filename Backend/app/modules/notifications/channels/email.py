"""Email adapter — STARTTLS SMTP via stdlib smtplib (no extra dep).

The blocking SMTP call is wrapped in ``asyncio.to_thread`` so the dispatch
coroutine never blocks the event loop.
"""
from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from loguru import logger

from app.core.config import settings
from app.modules.notifications.channels.base import (
    ChannelAdapter,
    ChannelMessage,
    ChannelResult,
)


class SmtpEmailAdapter(ChannelAdapter):
    name = "email"

    def is_configured(self) -> bool:
        return bool(settings.smtp_host and settings.smtp_from_email)

    async def send(self, msg: ChannelMessage) -> ChannelResult:
        if not self.is_configured():
            logger.warning("Email adapter not configured — skipping send")
            return ChannelResult(ok=False, error="not_configured")

        try:
            await asyncio.to_thread(self._send_blocking, msg)
        except (smtplib.SMTPException, OSError) as exc:
            return ChannelResult(ok=False, error=f"smtp_error:{exc.__class__.__name__}")
        return ChannelResult(ok=True)

    @staticmethod
    def _send_blocking(msg: ChannelMessage) -> None:
        em = EmailMessage()
        em["From"] = settings.smtp_from_email or ""
        em["To"] = msg.recipient
        em["Subject"] = msg.subject or "GESTION-EE"
        em.set_content(msg.message)

        context = ssl.create_default_context()
        with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(em)
