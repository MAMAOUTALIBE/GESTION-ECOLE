"""Phase 14 — SMS / USSD gateway abstraction.

Provider interface : Twilio, Africa's Talking, Orange Money SMS, Vonage…
La couche `provider` est un stub log-only par défaut ; en prod il suffit
de remplacer `_send_via_provider` par un appel HTTP au provider configuré.
"""
import logging
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from app.modules.auth.models import User
from app.modules.realtime.router import notify_user
from app.shared.deps import get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sms"])

SMS_WRITE_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
)


class SmsRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    to: str = Field(min_length=8, description="Destinataire format E.164 (+224...)")
    message: str = Field(min_length=1, max_length=480)  # 3 SMS concatenés max
    channel: Literal["SMS", "USSD", "WHATSAPP"] = "SMS"
    senderId: str | None = Field(default="GESTION-EE", max_length=11)


class SmsBulkRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    recipients: list[str] = Field(min_length=1, max_length=10000)
    message: str = Field(min_length=1, max_length=480)
    channel: Literal["SMS", "WHATSAPP"] = "SMS"


class SmsResponse(BaseModel):
    accepted: int
    rejected: int
    failures: list[dict] = []
    provider: str
    messageId: str | None = None


_E164 = re.compile(r"^\+\d{8,15}$")


@router.post(
    "/send",
    response_model=SmsResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*SMS_WRITE_ROLES))],
    summary="Envoie un SMS / USSD / WhatsApp à un destinataire",
)
async def send_sms(
    dto: SmsRequest, user: Annotated[User, Depends(get_current_user)],
) -> SmsResponse:
    if not _E164.match(dto.to):
        return SmsResponse(
            accepted=0, rejected=1,
            failures=[{"to": dto.to, "reason": "Format E.164 invalide"}],
            provider="stub",
        )
    msg_id = await _send_via_provider(dto.to, dto.message, dto.channel)
    # Notifier le user-émetteur via WebSocket que l'envoi est parti
    await notify_user(user.id, {
        "type": "SMS_SENT",
        "to": dto.to, "channel": dto.channel, "messageId": msg_id,
    })
    return SmsResponse(accepted=1, rejected=0, provider="stub", messageId=msg_id)


@router.post(
    "/bulk",
    response_model=SmsResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*SMS_WRITE_ROLES))],
    summary="Diffusion bulk (alerte ministérielle, communication parents)",
)
async def send_bulk(
    dto: SmsBulkRequest, user: Annotated[User, Depends(get_current_user)],
) -> SmsResponse:
    accepted = 0
    rejected = 0
    failures = []
    for to in dto.recipients:
        if _E164.match(to):
            await _send_via_provider(to, dto.message, dto.channel)
            accepted += 1
        else:
            rejected += 1
            failures.append({"to": to, "reason": "Format E.164 invalide"})
            if len(failures) >= 50:
                break
    return SmsResponse(
        accepted=accepted, rejected=rejected, failures=failures, provider="stub",
    )


async def _send_via_provider(to: str, message: str, channel: str) -> str:
    """Stub provider — log uniquement. En prod : Twilio/Africa's Talking."""
    fake_id = f"stub-{abs(hash(to + message)) % 10**10}"
    logger.info(
        "[SMS-STUB] %s → %s (%d chars) id=%s",
        channel, to, len(message), fake_id,
    )
    # TODO: remplacer par
    #   async with httpx.AsyncClient() as client:
    #       await client.post(provider_url, ...)
    return fake_id
