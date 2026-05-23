"""Channel adapter contract.

Each transport (SMS, WhatsApp, Email, Push, InApp) implements this interface.
The dispatcher picks the right adapter from the registry and awaits ``send``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChannelMessage:
    """Payload to send. ``recipient`` semantics depend on the channel:

    * SMS / WhatsApp / Push → phone number (E.164) or device token
    * Email                 → email address
    * InApp                 → User.id of the recipient
    """

    recipient: str
    message: str
    subject: str | None = None
    metadata: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ChannelResult:
    """Result returned by an adapter. ``provider_id`` is the upstream message
    id (Twilio SID, WhatsApp message id, FCM name, SMTP queue id…) when
    available — useful for delivery webhooks later.
    """

    ok: bool
    provider_id: str | None = None
    error: str | None = None


class ChannelAdapter(ABC):
    """Abstract base class — every concrete adapter inherits from this."""

    name: str  # set by subclass

    @abstractmethod
    def is_configured(self) -> bool:
        """True iff the adapter has all credentials it needs to actually send.

        Adapters returning False short-circuit to a ``ChannelResult(ok=False,
        error="not_configured")`` — this lets dev/staging environments skip
        real network calls without crashing.
        """

    @abstractmethod
    async def send(self, msg: ChannelMessage) -> ChannelResult:
        """Send ``msg`` via this transport. Must NOT raise — failures should
        be returned as ``ChannelResult(ok=False, error=...)``.
        """


def normalize_phone(value: str, default_country_code: str = "224") -> str:
    """Best-effort E.164 normalization for Guinean numbers.

    * Strips spaces, dashes, parentheses
    * Keeps a leading ``+`` if present
    * Otherwise prefixes the default country code (Guinea = 224)
    """
    cleaned = "".join(ch for ch in value if ch.isdigit() or ch == "+")
    if cleaned.startswith("+"):
        return cleaned
    if cleaned.startswith("00"):
        return "+" + cleaned[2:]
    if cleaned.startswith(default_country_code):
        return "+" + cleaned
    # Local format: prepend country code
    return f"+{default_country_code}{cleaned.lstrip('0')}"
