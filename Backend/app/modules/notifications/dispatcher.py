"""Dispatcher — picks the right ``ChannelAdapter`` for a CommunicationChannel.

Network-bound adapters (SMS / WhatsApp / Email / Push) are stateless and
held as module-level singletons. The InApp adapter is constructed per
request because it needs an SQLAlchemy session.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.channels.base import (
    ChannelAdapter,
    ChannelMessage,
    ChannelResult,
)
from app.modules.notifications.channels.email import SmtpEmailAdapter
from app.modules.notifications.channels.inapp import InAppAdapter
from app.modules.notifications.channels.push import FcmPushAdapter
from app.modules.notifications.channels.sms import TwilioSmsAdapter
from app.modules.notifications.channels.whatsapp import WhatsappCloudAdapter
from app.shared.enums import CommunicationChannel

# Stateless singletons
_SMS = TwilioSmsAdapter()
_WHATSAPP = WhatsappCloudAdapter()
_EMAIL = SmtpEmailAdapter()
_PUSH = FcmPushAdapter()


def get_adapter(
    channel: CommunicationChannel, session: AsyncSession | None = None
) -> ChannelAdapter | None:
    """Return the adapter for ``channel`` or ``None`` if not supported.

    PHONE has no automated transport (it's a manual call log) — callers
    should treat the communication as SENT immediately without dispatch.
    IN_APP needs ``session`` (raises ValueError if omitted).
    """
    if channel == CommunicationChannel.SMS:
        return _SMS
    if channel == CommunicationChannel.WHATSAPP:
        return _WHATSAPP
    if channel == CommunicationChannel.EMAIL:
        return _EMAIL
    if channel == CommunicationChannel.IN_APP:
        if session is None:
            raise ValueError("InApp adapter requires a DB session")
        return InAppAdapter(session)
    # PHONE → no transport, manual log only
    return None


async def dispatch(
    channel: CommunicationChannel,
    msg: ChannelMessage,
    *,
    session: AsyncSession | None = None,
) -> ChannelResult:
    """Send ``msg`` via ``channel``. Returns a ``ChannelResult`` (never raises)."""
    adapter = get_adapter(channel, session=session)
    if adapter is None:
        # PHONE → treat as ok, marked SENT immediately
        return ChannelResult(ok=True, provider_id="manual_phone_log")
    return await adapter.send(msg)
