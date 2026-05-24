"""Module 14 — Schemas Pydantic exposés par le router SMS / USSD."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.sms.enums import SmsDirection, SmsStatus


class SendSmsRequest(BaseModel):
    """Payload d'envoi simple — destinataire E.164 + corps."""

    model_config = ConfigDict(str_strip_whitespace=True)

    to: str = Field(min_length=4, max_length=20)
    body: str = Field(min_length=1, max_length=480)


class SendTemplatedRequest(BaseModel):
    """Payload d'envoi templated — user destinataire + clé i18n."""

    model_config = ConfigDict(str_strip_whitespace=True)

    userId: str = Field(min_length=1, max_length=30)
    templateKey: str = Field(min_length=1, max_length=120)
    variables: dict[str, str | int | float] = Field(default_factory=dict)


class SmsMessageOut(BaseModel):
    """Représentation publique d'un :class:`SmsMessage`."""

    id: str
    direction: SmsDirection
    to: str
    from_: str | None = Field(default=None, alias="from")
    body: str
    status: SmsStatus
    providerId: str | None
    errorMessage: str | None
    createdAt: datetime
    deliveredAt: datetime | None

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


class SmsListResponse(BaseModel):
    items: list[SmsMessageOut]
    total: int


class UssdCallbackRequest(BaseModel):
    """Format aligné sur la convention Africa's Talking / Orange : tous les
    opérateurs USSD majeurs en zone UEMOA exposent ces 4 champs."""

    model_config = ConfigDict(str_strip_whitespace=True)

    sessionId: str = Field(min_length=1, max_length=80)
    phoneNumber: str = Field(min_length=4, max_length=20)
    serviceCode: str | None = Field(default=None, max_length=20)
    text: str = Field(default="")


class SmsStats(BaseModel):
    """Compteurs agrégés pour le dashboard ministériel."""

    totalMessages: int
    sentLast24h: int
    failedLast24h: int
    ussdSessionsLast24h: int
    byStatus: dict[str, int]


class DeliveryReportRequest(BaseModel):
    """Webhook provider — delivery report d'un message déjà envoyé."""

    model_config = ConfigDict(str_strip_whitespace=True)

    providerId: str = Field(min_length=1, max_length=80)
    status: SmsStatus
    errorMessage: str | None = None
