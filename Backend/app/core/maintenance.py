"""Module 15 — Middleware HTTP qui force la lecture seule sur la plateforme.

Quand le flag ``admin:maintenance`` est posé dans Redis (cf.
``AdminService.enable_maintenance_mode``), toute requête HTTP "write"
(POST / PUT / PATCH / DELETE) est rejetée en **503 Service Unavailable**
avec un body JSON explicite.

Exemptions
----------
On laisse passer les routes qui doivent rester opérationnelles même en
maintenance, en particulier :

* ``/health``, ``/ready``       — probes Kubernetes (sinon le pod
  finirait par être tué juste parce qu'on a posé le flag).
* ``/api/admin/maintenance/*``  — sinon on ne pourrait plus désactiver
  la maintenance ! (chicken-and-egg)
* ``/api/auth/login``           — les admins doivent pouvoir se
  reconnecter pour disable le mode si leur session a expiré.

La liste est volontairement courte et explicite. Tout le reste (y compris
``/api/auth/logout``) est bloqué : c'est le comportement attendu d'un
"read-only mode" — on coupe toute écriture, point.

Chemin chaud
------------
On lit le flag depuis Redis (``GET admin:maintenance``) pour ne jamais
toucher Postgres pendant le middleware. Si Redis est down, on log et on
laisse passer (fail-open : ne pas pénaliser le service à cause d'une
panne du panneau admin).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from app.modules.admin.service import MAINTENANCE_REDIS_KEY

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Préfixes ou paths exacts toujours autorisés (même en maintenance).
EXEMPT_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/ready",
    "/metrics",
    "/api/admin/maintenance",
    "/api/auth/login",
)


def _path_is_exempt(path: str) -> bool:
    """True si `path` est exempté de maintenance (match exact ou sous-segment).

    On évite délibérément un ``startswith`` naïf qui matcherait ``/health-x``
    sur ``/health`` : on n'accepte que ``== prefix`` ou ``prefix + "/..."``.
    """
    return any(
        path == prefix or path.startswith(prefix + "/")
        for prefix in EXEMPT_PATH_PREFIXES
    )


class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    """ASGI middleware : 503 sur les writes quand la maintenance est active."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method not in WRITE_METHODS:
            return await call_next(request)
        if _path_is_exempt(request.url.path):
            return await call_next(request)

        try:
            from app.core.redis import get_redis
            redis = get_redis()
            flag = await redis.get(MAINTENANCE_REDIS_KEY)
        except Exception as exc:  # pragma: no cover - depends on infra
            logger.warning("maintenance middleware: redis check failed: {}", exc)
            return await call_next(request)

        if flag in {"1", "true", "True"}:
            return JSONResponse(
                status_code=HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "code": "maintenance_mode",
                    "message": (
                        "Plateforme en mode maintenance — écritures temporairement "
                        "désactivées. Réessayez dans quelques minutes."
                    ),
                    "extra": {},
                },
            )
        return await call_next(request)


__all__ = ["EXEMPT_PATH_PREFIXES", "WRITE_METHODS", "MaintenanceModeMiddleware"]
