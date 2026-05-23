"""Async geocoding via Nominatim (OpenStreetMap). Free, no API key required.

Production note: Nominatim's public instance enforces ~1 req/s. For mass
geocoding (thousands of addresses), self-host a Nominatim server or switch
to a paid provider (Google Maps, Mapbox).
"""
from typing import Any

import httpx

from app.core.celery_app import celery_app

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "GESTION-EE/1.0 (contact@gmd2025.org)"


@celery_app.task(name="geocoding.geocode_address", bind=True, max_retries=3)
def geocode_address(self, address: str) -> dict[str, Any]:
    """Resolve `address` → {lat, lng} via Nominatim. Idempotent retry on 429/5xx."""
    try:
        resp = httpx.get(
            NOMINATIM_URL,
            params={
                "q": address,
                "format": "json",
                "limit": 1,
                "countrycodes": "gn",  # Guinea
            },
            headers={"User-Agent": USER_AGENT},
            timeout=10.0,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return {"address": address, "found": False}
        first = results[0]
        return {
            "address": address,
            "found": True,
            "latitude": float(first["lat"]),
            "longitude": float(first["lon"]),
            "displayName": first.get("display_name"),
        }
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        # Exponential backoff: 30s, 60s, 120s
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


@celery_app.task(name="geocoding.noop")
def noop() -> str:
    """Placeholder retained for compatibility with existing worker discovery."""
    return "geocoding.noop ok"
