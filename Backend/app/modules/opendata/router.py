"""Module 12 — Portail Open Data (statistiques publiques anonymisées).

Endpoints **PUBLICS** sans authentification — données agrégées uniquement,
aucun accès aux noms d'élèves/enseignants. Conforme RGPD-like, sous
licence ouverte (CC-BY-4.0 par défaut).

Endpoints
---------
* ``GET /api/opendata/datasets``               — catalogue complet.
* ``GET /api/opendata/datasets/{key}``         — métadonnées d'un dataset.
* ``GET /api/opendata/datasets/{key}/data``    — données JSON ou CSV.
* ``GET /api/opendata/stats``                  — compteurs anonymes.

Rate limit
----------
60 requêtes / minute / IP sur les endpoints data-heavy
(``/datasets/{key}/data``). On utilise :class:`RateLimiter` de
``app.core.rate_limit`` (Redis fixed-window) pour rester cohérent avec
les autres modules (auth, assistant).
"""
from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.core.exceptions import NotFoundError, RateLimitedError
from app.core.proxy import client_ip
from app.core.rate_limit import RateLimiter
from app.core.redis import get_redis
from app.modules.opendata.anonymization import hash_ip
from app.modules.opendata.schemas import (
    DatasetListResponse,
    DatasetMetadata,
    OpendataStats,
)
from app.modules.opendata.service import OpendataService
from app.shared.deps import DbSession

router = APIRouter(tags=["opendata"])


# Rate limit : 60 requêtes / minute / IP sur les endpoints data.
OPENDATA_IP_LIMIT: Final = 60
OPENDATA_IP_WINDOW_S: Final = 60


def _svc(session: DbSession) -> OpendataService:
    return OpendataService(session)


Svc = Annotated[OpendataService, Depends(_svc)]


async def _enforce_rate_limit(request: Request) -> str:
    """Vérifie le quota Redis 60/min/IP et renvoie l'IP utilisée.

    Renvoie l'IP (string) pour que les endpoints l'utilisent ensuite
    pour calculer ``ipHash`` (audit anonyme). Lève
    :class:`RateLimitedError` (HTTP 429) si la limite est dépassée.
    """
    ip = client_ip(request) or "unknown"
    redis = get_redis()
    limiter = RateLimiter(redis)
    result = await limiter.check_and_increment(
        f"opendata:ip:{ip}",
        OPENDATA_IP_LIMIT,
        OPENDATA_IP_WINDOW_S,
    )
    if not result.allowed:
        raise RateLimitedError(
            detail=(
                "Limite de débit atteinte (60 requêtes / minute). "
                "Réessayez dans une minute."
            ),
            extra={
                "limit": OPENDATA_IP_LIMIT,
                "windowSeconds": OPENDATA_IP_WINDOW_S,
            },
        )
    return ip


# ===========================================================================
# 1. GET /datasets — catalogue
# ===========================================================================
@router.get(
    "/datasets",
    response_model=DatasetListResponse,
    summary="Catalogue des datasets open data publics (sans auth)",
)
async def list_datasets(
    request: Request,
    service: Svc,
) -> DatasetListResponse:
    """Retourne le catalogue complet (6 datasets MVP)."""
    await _enforce_rate_limit(request)
    items = await service.list_datasets()
    return DatasetListResponse(items=items, total=len(items))


# ===========================================================================
# 2. GET /datasets/{key} — métadonnées d'un dataset
# ===========================================================================
@router.get(
    "/datasets/{key}",
    response_model=DatasetMetadata,
    summary="Métadonnées détaillées d'un dataset",
)
async def get_dataset_metadata(
    key: str,
    request: Request,
    service: Svc,
) -> DatasetMetadata:
    await _enforce_rate_limit(request)
    meta = await service.get_dataset_metadata(key)
    if meta is None:
        raise NotFoundError(detail=f"Dataset inconnu: {key}")
    return meta


# ===========================================================================
# 3. GET /datasets/{key}/data — données brutes (JSON/CSV)
# ===========================================================================
@router.get(
    "/datasets/{key}/data",
    summary="Télécharge les données d'un dataset (JSON ou CSV)",
)
async def get_dataset_data(
    key: str,
    request: Request,
    service: Svc,
    format: Annotated[str, Query(pattern="^(json|csv)$")] = "json",
) -> Response:
    """Renvoie le payload du dataset + enregistre un audit anonyme.

    Le format est validé par le pattern de Query (``json|csv``). Pour
    ``csv`` on positionne ``Content-Disposition: attachment`` pour
    déclencher le téléchargement côté navigateur.
    """
    ip = await _enforce_rate_limit(request)

    result = await service.get_dataset_data(key, format=format)
    if result is None:
        raise NotFoundError(detail=f"Dataset inconnu: {key}")
    payload, content_type = result

    # Audit append-only — anonyme (hash IP, jamais l'IP en clair).
    await service.log_download(
        key=key, ip_hash=hash_ip(ip), format=format,
    )

    headers: dict[str, str] = {}
    if format == "csv":
        headers["Content-Disposition"] = (
            f'attachment; filename="{key}.csv"'
        )
    return Response(
        content=payload,
        media_type=content_type,
        status_code=status.HTTP_200_OK,
        headers=headers,
    )


# ===========================================================================
# 4. GET /stats — compteurs anonymes
# ===========================================================================
@router.get(
    "/stats",
    response_model=OpendataStats,
    summary="Statistiques anonymes des téléchargements",
)
async def get_stats(
    request: Request,
    service: Svc,
) -> OpendataStats:
    await _enforce_rate_limit(request)
    return await service.get_stats()
