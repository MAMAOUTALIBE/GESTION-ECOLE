"""Observability primitives — request id propagation, structured logging
binding, and business-level Prometheus counters.

These are deliberately separate from ``app/core/celery_app.py`` and the
plain ``loguru`` config so the wiring in ``main.py`` stays a one-liner.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from loguru import logger
from prometheus_client import Counter
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------
# Business-level Prometheus counters (aggregated across all instances)
# ---------------------------------------------------------------------
# Auth
auth_login_total = Counter(
    "gestionee_auth_login_total",
    "Login attempts grouped by outcome",
    labelnames=("result",),  # success | invalid | inactive
)

# Security fix C-5 — observability for Redis-backed auth checks failing.
# Both counters share the same surface: bump on EVERY caught Redis error
# in get_current_user / rate_limit. Alerting threshold: > 0 over 1 min
# is a P1 (auth degradation or Redis outage).
auth_revocation_check_failed_total = Counter(
    "gestionee_auth_revocation_check_failed_total",
    "Times the JWT JTI revocation check could not be performed (Redis down)",
)
auth_rate_limit_check_failed_total = Counter(
    "gestionee_auth_rate_limit_check_failed_total",
    "Times the auth rate-limit check could not be performed (Redis down)",
)

# QR scans (attendance)
attendance_scan_total = Counter(
    "gestionee_attendance_scan_total",
    "Attendance scan attempts grouped by outcome",
    labelnames=("result",),  # ok | duplicate | not_found | forbidden
)

# Notifications
notification_dispatch_total = Counter(
    "gestionee_notification_dispatch_total",
    "Parent communication dispatch attempts",
    labelnames=("channel", "result"),  # channel ∈ SMS|WHATSAPP|..., result ∈ ok|failed
)

# Imports
import_commit_total = Counter(
    "gestionee_import_commit_total",
    "Mass-import commit attempts grouped by kind + result",
    labelnames=("kind", "result"),  # kind ∈ students|teachers|schools, result ∈ ok|failed
)

# Census — Module 2 (dédoublonnage)
# Toute demande de vérification de doublons (fait sur la hot-path création).
census_duplicate_check_total = Counter(
    "gestionee_census_duplicate_check_total",
    "Total des appels au moteur de dédoublonnage census",
    labelnames=("entity",),  # entity ∈ student | teacher
)
# Création bloquée car un doublon HIGH a été détecté sans le flag force.
census_duplicate_blocked_total = Counter(
    "gestionee_census_duplicate_blocked_total",
    "Créations census bloquées par le seuil de doublons",
    labelnames=("entity", "level"),  # level ∈ HIGH (le seul qui bloque pour l'instant)
)
# Fusion d'entités census (merge_students). Stratégique : audit + suivi UX.
census_merge_total = Counter(
    "gestionee_census_merge_total",
    "Total des fusions de fiches census",
    labelnames=("entity", "result"),  # result ∈ ok | not_found | forbidden
)


# ---------------------------------------------------------------------
# Request ID middleware — propagates X-Request-Id and binds it to loguru
# ---------------------------------------------------------------------
REQUEST_ID_HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a request id to every request and echo it back to the client.

    * If the caller already sent ``X-Request-Id``, keep it (gateway / LB friendly)
    * Otherwise mint a fresh uuid4 hex
    * The id is also stored on ``request.state.request_id`` and bound to loguru
      via ``logger.bind(request_id=...)`` for the duration of the request — any
      `logger.info(...)` call inside the handler picks it up automatically.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid4().hex
        request.state.request_id = rid
        with logger.contextualize(request_id=rid):
            response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = rid
        return response
