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
from prometheus_client import Counter, Histogram
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


# ---------------------------------------------------------------------------
# Module 4 — génération PDF asynchrone des bulletins
# ---------------------------------------------------------------------------
# Une demande de génération (HTTP POST). On compte aussi les hits "cache" :
# si un bulletin est déjà DONE, on incrémente quand même cette métrique (et
# `reports_pdf_completed_total{status="cache_hit"}`).
reports_pdf_requested_total = Counter(
    "gestionee_reports_pdf_requested_total",
    "Demandes de génération de bulletin PDF (incluant cache hits)",
)
# Fin de cycle d'un bulletin — labellé par le résultat. ``status`` ∈
# ``done | failed | cache_hit``. ``done`` = upload S3 réussi ce coup-ci ;
# ``cache_hit`` = la requête HTTP a renvoyé un DONE pré-existant ;
# ``failed`` = le worker a abandonné après retries.
reports_pdf_completed_total = Counter(
    "gestionee_reports_pdf_completed_total",
    "Bulletins PDF terminés (par résultat)",
    labelnames=("status",),
)
# Durée du rendu côté worker (rendu HTML + WeasyPrint + upload S3). Histo en
# secondes avec des buckets adaptés au temps de génération (centaines de ms à
# quelques secondes pour des bulletins riches en grades).
reports_pdf_duration_seconds = Histogram(
    "gestionee_reports_pdf_duration_seconds",
    "Durée totale de génération d'un bulletin PDF (rendu + upload)",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
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
