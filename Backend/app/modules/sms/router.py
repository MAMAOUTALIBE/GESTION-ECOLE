"""Module 14 — Routeur FastAPI : SMS outbound + USSD inbound.

Endpoints
---------
* ``POST /api/sms/send`` — envoi d'un SMS direct. RBAC ≥ SCHOOL_DIRECTOR.
* ``POST /api/sms/send-templated`` — envoi via template i18n. Même RBAC.
* ``GET  /api/sms/messages`` — liste paginée des messages. RBAC ≥ SCHOOL_DIRECTOR.
* ``POST /api/sms/ussd/callback`` — PUBLIC (webhook opérateur). Signature
  HMAC optionnelle via env ``USSD_HMAC_SECRET`` + header ``X-USSD-Signature``.
* ``POST /api/sms/delivery-report`` — PUBLIC (webhook provider). Met à jour
  le statut DELIVERED/FAILED d'un message déjà envoyé.
* ``GET  /api/sms/stats`` — KPIs admin. RBAC ≥ REGIONAL_ADMIN.

Anti-spam USSD
--------------
On limite à 5 sessions USSD / minute / numéro côté Redis. Au-delà, on
renvoie une réponse ``END`` polie pour le parent (et on log côté serveur).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Final

from fastapi import (
    APIRouter,
    Depends,
    Header,
    Query,
    Request,
    Response,
    status,
)
from loguru import logger
from sqlalchemy import func, select

from app.core.exceptions import NotFoundError
from app.core.rate_limit import RateLimiter
from app.core.redis import get_redis
from app.modules.auth.models import User
from app.modules.sms.enums import SmsDirection, SmsStatus
from app.modules.sms.models import SmsMessage, UssdSession
from app.modules.sms.schemas import (
    DeliveryReportRequest,
    SendSmsRequest,
    SendTemplatedRequest,
    SmsListResponse,
    SmsMessageOut,
    SmsStats,
    UssdCallbackRequest,
)
from app.modules.sms.service import SmsService
from app.modules.sms.ussd import handle_ussd, verify_signature
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

router = APIRouter(tags=["sms"])


SMS_WRITE_ROLES: Final = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
)

SMS_STATS_ROLES: Final = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
)

# Rate limit USSD : 5 / minute / numéro
USSD_PHONE_LIMIT: Final = 5
USSD_PHONE_WINDOW_S: Final = 60


# ---------------------------------------------------------------------------
# 1. POST /send — envoi simple
# ---------------------------------------------------------------------------
@router.post(
    "/send",
    response_model=SmsMessageOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*SMS_WRITE_ROLES))],
    summary="Envoie un SMS à un destinataire (RBAC >= SCHOOL_DIRECTOR)",
)
async def send_sms(
    dto: SendSmsRequest,
    session: DbSession,
    user: Annotated[User, Depends(get_current_user)],
) -> SmsMessageOut:
    service = SmsService(session)
    message = await service.send_sms(to=dto.to, body=dto.body, actor=user)
    return _to_out(message)


# ---------------------------------------------------------------------------
# 2. POST /send-templated — envoi avec template i18n
# ---------------------------------------------------------------------------
@router.post(
    "/send-templated",
    response_model=SmsMessageOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_roles(*SMS_WRITE_ROLES))],
    summary="Envoie un SMS rendu par template i18n",
)
async def send_templated(
    dto: SendTemplatedRequest,
    session: DbSession,
    user: Annotated[User, Depends(get_current_user)],
) -> SmsMessageOut:
    service = SmsService(session)
    message = await service.send_templated(
        user_id=dto.userId,
        template_key=dto.templateKey,
        variables=dict(dto.variables),
        actor=user,
    )
    return _to_out(message)


# ---------------------------------------------------------------------------
# 3. GET /messages — liste paginée
# ---------------------------------------------------------------------------
@router.get(
    "/messages",
    response_model=SmsListResponse,
    dependencies=[Depends(require_roles(*SMS_WRITE_ROLES))],
    summary="Liste paginée des messages SMS",
)
async def list_messages(
    session: DbSession,
    direction: SmsDirection | None = None,
    sms_status: Annotated[SmsStatus | None, Query(alias="status")] = None,
    to: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> SmsListResponse:
    service = SmsService(session)
    items, total = await service.list_messages(
        direction=direction, status=sms_status, to=to,
        limit=limit, offset=offset,
    )
    return SmsListResponse(
        items=[_to_out(m) for m in items], total=total,
    )


# ---------------------------------------------------------------------------
# 4. POST /ussd/callback — PUBLIC, webhook opérateur
# ---------------------------------------------------------------------------
@router.post(
    "/ussd/callback",
    summary="Webhook USSD entrant (PUBLIC). HMAC optionnel.",
)
async def ussd_callback(
    request: Request,
    session: DbSession,
    x_ussd_signature: Annotated[str | None, Header()] = None,
) -> Response:
    """Reçoit un callback opérateur USSD et renvoie la string de réponse.

    Conformément aux conventions Africa's Talking / Orange, on renvoie
    ``Content-Type: text/plain`` et la première ligne est ``CON ...`` ou
    ``END ...``. La signature HMAC est optionnelle : si la variable
    ``USSD_HMAC_SECRET`` est définie, on rejette les requêtes mal signées
    avec HTTP 401.
    """
    raw_body = await request.body()
    if not verify_signature(raw_body, x_ussd_signature):
        return Response(
            content="END Signature invalide.",
            media_type="text/plain",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Parse manuel : on accepte JSON ET form-encoded (les opérateurs
    # alternent selon les pays).
    payload = await _parse_ussd_payload(request, raw_body)
    try:
        dto = UssdCallbackRequest.model_validate(payload)
    except Exception as exc:
        logger.warning("ussd_callback bad payload: {}", exc)
        return Response(
            content="END Requete invalide.",
            media_type="text/plain",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # ---- Rate limit (5 sessions / minute / numéro) ----
    redis = get_redis()
    limiter = RateLimiter(redis)
    result = await limiter.check_and_increment(
        f"ussd:phone:{dto.phoneNumber}",
        USSD_PHONE_LIMIT, USSD_PHONE_WINDOW_S,
    )
    if not result.allowed:
        return Response(
            content="END Trop de tentatives. Reessayez dans 1 minute.",
            media_type="text/plain",
            status_code=status.HTTP_200_OK,  # opérateur attend du 200
        )

    response_text = await handle_ussd(
        session_id=dto.sessionId,
        phone=dto.phoneNumber,
        text=dto.text,
        service_code=dto.serviceCode,
        db=session,
    )
    return Response(
        content=response_text,
        media_type="text/plain",
        status_code=status.HTTP_200_OK,
    )


async def _parse_ussd_payload(
    request: Request, raw_body: bytes,
) -> dict:
    """Accepte JSON ou form-urlencoded."""
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        import json
        try:
            return json.loads(raw_body.decode("utf-8") or "{}")
        except Exception:
            return {}
    # form-encoded ou autre → on lit via request.form()
    try:
        form = await request.form()
        return {k: str(v) for k, v in form.items()}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 5. POST /delivery-report — webhook provider (delivery status)
# ---------------------------------------------------------------------------
@router.post(
    "/delivery-report",
    response_model=SmsMessageOut,
    summary="Webhook provider — update delivery status",
)
async def delivery_report(
    dto: DeliveryReportRequest,
    session: DbSession,
) -> SmsMessageOut:
    service = SmsService(session)
    try:
        message = await service.update_status_from_callback(
            provider_id=dto.providerId,
            status=dto.status,
            error_message=dto.errorMessage,
        )
    except NotFoundError:
        # Ne pas renvoyer 404 — sinon le provider va retry indéfiniment.
        # On loggue et on renvoie un 200 vide.
        logger.warning(
            "delivery-report: unknown providerId={}", dto.providerId,
        )
        raise
    return _to_out(message)


# ---------------------------------------------------------------------------
# 6. GET /stats — KPIs admin
# ---------------------------------------------------------------------------
@router.get(
    "/stats",
    response_model=SmsStats,
    dependencies=[Depends(require_roles(*SMS_STATS_ROLES))],
    summary="Statistiques SMS / USSD (RBAC >= REGIONAL_ADMIN)",
)
async def get_stats(session: DbSession) -> SmsStats:
    since_24h = datetime.now(UTC) - timedelta(hours=24)

    total_q = select(func.count()).select_from(SmsMessage)
    total = (await session.execute(total_q)).scalar_one()

    sent_q = (
        select(func.count())
        .select_from(SmsMessage)
        .where(SmsMessage.status == SmsStatus.SENT)
        .where(SmsMessage.createdAt >= since_24h)
    )
    sent_24 = (await session.execute(sent_q)).scalar_one()

    failed_q = (
        select(func.count())
        .select_from(SmsMessage)
        .where(SmsMessage.status == SmsStatus.FAILED)
        .where(SmsMessage.createdAt >= since_24h)
    )
    failed_24 = (await session.execute(failed_q)).scalar_one()

    ussd_q = (
        select(func.count())
        .select_from(UssdSession)
        .where(UssdSession.createdAt >= since_24h)
    )
    ussd_24 = (await session.execute(ussd_q)).scalar_one()

    by_status_q = (
        select(SmsMessage.status, func.count())
        .group_by(SmsMessage.status)
    )
    by_status_rows = (await session.execute(by_status_q)).all()
    by_status = {row[0].value: row[1] for row in by_status_rows}

    return SmsStats(
        totalMessages=int(total),
        sentLast24h=int(sent_24),
        failedLast24h=int(failed_24),
        ussdSessionsLast24h=int(ussd_24),
        byStatus=by_status,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_out(message: SmsMessage) -> SmsMessageOut:
    return SmsMessageOut(
        id=message.id,
        direction=message.direction,
        to=message.to_,
        from_=message.from_,
        body=message.body,
        status=message.status,
        providerId=message.providerId,
        errorMessage=message.errorMessage,
        createdAt=message.createdAt,
        deliveredAt=message.deliveredAt,
    )
