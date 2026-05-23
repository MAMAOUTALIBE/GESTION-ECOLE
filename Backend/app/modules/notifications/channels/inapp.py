"""In-app channel — writes a row in the existing Notification table.

Recipient is interpreted as ``User.id``. Used when the parent (or any
recipient) has an active session in the Angular frontend and the message
should appear in the bell-icon dropdown.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.channels.base import (
    ChannelAdapter,
    ChannelMessage,
    ChannelResult,
)
from app.modules.workflow.models import Notification
from app.shared.enums import NotificationType


class InAppAdapter(ChannelAdapter):
    """Persists the message into the in-app inbox.

    Unlike the network-bound adapters this one needs a DB session — it's
    instantiated per-request from the dispatcher (see service.py) rather than
    held as a singleton in the registry.
    """

    name = "inapp"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def is_configured(self) -> bool:
        return True  # always available

    async def send(self, msg: ChannelMessage) -> ChannelResult:
        notif = Notification(
            recipientUserId=msg.recipient,
            senderUserId=(msg.metadata or {}).get("senderUserId"),
            title=msg.subject or "Message",
            message=msg.message,
            type=NotificationType.MESSAGE,
        )
        self.session.add(notif)
        await self.session.flush()
        return ChannelResult(ok=True, provider_id=notif.id)
