"""Module 18 — Routeur FastAPI : portail parent multi-canal.

Endpoints
---------
* ``POST /api/parent-portal/whatsapp/webhook`` — PUBLIC (webhook Cloud API).
  HMAC obligatoire si ``WHATSAPP_HMAC_SECRET`` est défini (env). Sinon
  on accepte sans contrôle (mode dev / test).
* ``GET  /api/parent-portal/overview/{phone_hash}`` — PUBLIC, JSON,
  rate-limit 20/min/phone-hash (anti-scrape).
* ``GET  /api/parent-portal/parent/{phone_hash}`` — PUBLIC, HTML léger
  (Jinja2 string template embarqué). Anonymisé : initiales seulement.

Rationale du rate-limit côté hash : le hash est dans l'URL → un
attaquant qui forcerait des hashes aléatoires consommerait quand même
notre Redis. On garde les 20/min par hash, et c'est suffisant pour les
usages parent légitimes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Annotated, Final

from fastapi import APIRouter, Header, Request, Response, status
from fastapi import Path as FPath
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from loguru import logger

from app.core.rate_limit import RateLimiter
from app.core.redis import get_redis
from app.modules.parent_portal.schemas import (
    ParentOverview,
    WhatsAppReplyOut,
)
from app.modules.parent_portal.service import ParentPortalService
from app.shared.deps import DbSession

router = APIRouter(tags=["parent-portal"])

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
OVERVIEW_LIMIT: Final = 20
OVERVIEW_WINDOW_S: Final = 60

# Jinja2 env — sandboxé sur le sous-dossier `templates/`. Autoescape HTML
# par défaut (anti-XSS). On garde un singleton process-wide.
_TEMPLATES_DIR = Path(__file__).parent / "templates"
_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


# ---------------------------------------------------------------------------
# 1. POST /whatsapp/webhook — PUBLIC, HMAC obligatoire si secret env présent
# ---------------------------------------------------------------------------
@router.post(
    "/whatsapp/webhook",
    response_model=WhatsAppReplyOut,
    summary="Webhook WhatsApp entrant (PUBLIC, HMAC obligatoire si secret env).",
)
async def whatsapp_webhook(
    request: Request,
    session: DbSession,
    x_whatsapp_signature: Annotated[
        str | None, Header(alias="X-WhatsApp-Signature")
    ] = None,
) -> WhatsAppReplyOut | Response:
    """Reçoit un message WhatsApp INBOUND.

    Le corps attendu :
        {"phoneNumber": "+224...", "body": "moyenne", "messageId": "wamid.XXX"}

    Si ``WHATSAPP_HMAC_SECRET`` est défini en env, on vérifie
    ``X-WhatsApp-Signature`` (HMAC-SHA256 hex du corps brut). Sinon, on
    accepte tel quel (mode dev / test).
    """
    raw_body = await request.body()
    if not _verify_hmac(raw_body, x_whatsapp_signature):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"code": "unauthorized", "message": "Signature invalide"},
        )

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception as exc:
        logger.warning("whatsapp_webhook bad json: {}", exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"code": "invalid_payload", "message": str(exc)},
        )

    phone_number = (payload.get("phoneNumber") or "").strip()
    body = (payload.get("body") or "").strip()
    message_id = (payload.get("messageId") or "").strip()
    if not (phone_number and message_id):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": "invalid_payload",
                "message": "phoneNumber et messageId requis",
            },
        )

    service = ParentPortalService(session)
    return await service.handle_whatsapp_message(
        phone_number=phone_number, body=body, message_id=message_id,
    )


# ---------------------------------------------------------------------------
# 2. GET /overview/{phone_hash} — PUBLIC + rate-limit 20/min/hash
# ---------------------------------------------------------------------------
@router.get(
    "/overview/{phone_hash}",
    response_model=ParentOverview,
    summary="Vue parent JSON par hash de numéro (PUBLIC, rate-limit 20/min).",
)
async def get_overview(
    session: DbSession,
    phone_hash: Annotated[str, FPath(min_length=8, max_length=128)],
) -> ParentOverview | Response:
    if not await _rate_limit_ok(phone_hash):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "code": "rate_limited",
                "message": "Trop de requetes. Reessayez dans 1 minute.",
            },
        )
    service = ParentPortalService(session)
    return await service.get_parent_overview(phone_hash)


# ---------------------------------------------------------------------------
# 3. GET /parent/{phone_hash} — PUBLIC HTML léger (Jinja2)
# ---------------------------------------------------------------------------
@router.get(
    "/parent/{phone_hash}",
    response_class=HTMLResponse,
    summary="Page parent HTML légère (PUBLIC, anonymisée).",
)
async def get_parent_page(
    session: DbSession,
    phone_hash: Annotated[str, FPath(min_length=8, max_length=128)],
) -> HTMLResponse:
    if not await _rate_limit_ok(phone_hash):
        return HTMLResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=(
                "<!doctype html><html><body>"
                "<p>Trop de requetes. Reessayez dans 1 minute.</p>"
                "</body></html>"
            ),
        )
    service = ParentPortalService(session)
    overview = await service.get_parent_overview(phone_hash)
    template = _JINJA_ENV.get_template("parent_overview.html")
    rendered = template.render(
        phoneHash=overview.phoneHash,
        childrenCount=overview.childrenCount,
        children=overview.children,
        upcomingEventNote=overview.upcomingEventNote,
    )
    return HTMLResponse(content=rendered, status_code=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Helpers — HMAC + rate-limit
# ---------------------------------------------------------------------------
def _verify_hmac(raw_body: bytes, provided_signature: str | None) -> bool:
    """Vérifie ``HMAC-SHA256(secret, raw_body)`` hex.

    Activé uniquement si ``WHATSAPP_HMAC_SECRET`` est défini ET non-vide.
    Sinon on accepte sans contrôle.
    """
    secret = (os.getenv("WHATSAPP_HMAC_SECRET") or "").strip()
    if not secret:
        return True
    if not provided_signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, provided_signature.strip())


async def _rate_limit_ok(phone_hash: str) -> bool:
    """20 requêtes / minute / phone_hash. Fail-open si Redis down."""
    try:
        redis = get_redis()
    except Exception:  # pragma: no cover - depends on env
        return True
    limiter = RateLimiter(redis)
    result = await limiter.check_and_increment(
        f"parent_portal:overview:{phone_hash}",
        OVERVIEW_LIMIT, OVERVIEW_WINDOW_S,
    )
    return result.allowed
