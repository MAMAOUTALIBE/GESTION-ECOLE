"""Walking isochrones — circular Haversine approximation (MVP).

Why an approximation
--------------------
A true walking isochrone needs a routable street network (OSRM, Valhalla,
GraphHopper), which we don't run locally. For Module 5 MVP we approximate
each isochrone as a circle whose radius equals ``minutes * speed_kmh / 60``
in metres. This is a reasonable upper bound for flat terrain — the polygon
the user sees is "the area you *could* reach in N minutes at 5 km/h *if no
road, slope or building got in the way*". Sufficient to drive a school-
placement heuristic and reasonable enough to ship the map UI.

Module 17 (Flutter mobile) will integrate Valhalla and replace this stub
with real routed isochrones. The endpoint contract is intentionally stable:
``FeatureCollection`` of ``Polygon`` features with ``timeMin`` properties.

Coordinate math
---------------
We approximate the circle on a sphere via the standard equirectangular
formulation:

    Δlat ≈ Δm / 111_320
    Δlng ≈ Δm / (111_320 * cos(lat_radians))

Good enough for radii <= 10 km at any non-polar latitude (the maximum we
expose is 120 min * 5 km/h = 10 km).
"""
from __future__ import annotations

import math
from typing import Any, Final

# Walking pace defaults (WHO baseline for adult, level terrain).
DEFAULT_SPEED_KMH: Final[float] = 5.0
DEFAULT_INTERVALS_MIN: Final[tuple[int, ...]] = (15, 30, 45, 60)

# Vertex count for the polygon approximation. 64 keeps the shape smooth
# without bloating the payload (8 kB FeatureCollection at 4 intervals).
_POLYGON_VERTICES: Final[int] = 64

# Hard caps protecting the API from absurd requests. 120 min @ 5 km/h = 10 km
# circle which is already a generous walking horizon.
MAX_MINUTES: Final[int] = 120

# WGS84 metres per degree of latitude at the equator (constant within the
# precision we need; the actual figure varies from 110.6 km at the equator
# to 111.7 km at the poles).
_METERS_PER_DEG_LAT: Final[float] = 111_320.0


def _radius_meters(minutes: int, speed_kmh: float) -> float:
    """Convert a duration + speed into a metre radius."""
    if minutes <= 0:
        raise ValueError(f"minutes must be positive, got {minutes}")
    if speed_kmh <= 0:
        raise ValueError(f"speed_kmh must be positive, got {speed_kmh}")
    return (minutes / 60.0) * speed_kmh * 1000.0


def _circle_polygon(
    lat: float, lon: float, radius_m: float, vertices: int = _POLYGON_VERTICES
) -> list[list[float]]:
    """Build a closed GeoJSON ring (one outer linear ring, RFC 7946).

    Coordinates are returned as ``[lon, lat]`` pairs, with the first and last
    points repeated (closure required by the spec).
    """
    if vertices < 3:
        raise ValueError(f"vertices must be >= 3, got {vertices}")
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"latitude out of range, got {lat}")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"longitude out of range, got {lon}")

    lat_step = radius_m / _METERS_PER_DEG_LAT
    # Avoid division by zero near the poles (singular point).
    cos_lat = math.cos(math.radians(lat))
    cos_lat = max(cos_lat, 1e-6)
    lon_step = radius_m / (_METERS_PER_DEG_LAT * cos_lat)

    ring: list[list[float]] = []
    for i in range(vertices):
        angle = 2.0 * math.pi * (i / vertices)
        dlat = lat_step * math.sin(angle)
        dlon = lon_step * math.cos(angle)
        ring.append([round(lon + dlon, 6), round(lat + dlat, 6)])
    # Close the ring.
    ring.append(ring[0])
    return ring


def compute_walking_isochrone(
    lat: float,
    lon: float,
    minutes: int,
    speed_kmh: float = DEFAULT_SPEED_KMH,
) -> dict[str, Any]:
    """Return a GeoJSON ``Polygon`` for a single isochrone.

    Properties carried by the polygon:
    * ``timeMin``      — duration in minutes
    * ``radiusMeters`` — equivalent walk radius
    * ``speedKmh``     — pace assumed
    * ``approximation``— always ``"haversine-circle"`` (Module 17 will switch
                         to ``"osrm-isodistance"`` when available).
    """
    if minutes > MAX_MINUTES:
        raise ValueError(
            f"minutes capped at {MAX_MINUTES} (got {minutes}) to bound the "
            "Haversine approximation error"
        )
    radius = _radius_meters(minutes, speed_kmh)
    ring = _circle_polygon(lat, lon, radius)
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {
            "timeMin": minutes,
            "radiusMeters": round(radius, 1),
            "speedKmh": speed_kmh,
            "approximation": "haversine-circle",
        },
    }


def isochrone_set(
    lat: float,
    lon: float,
    intervals_min: list[int] | tuple[int, ...] = DEFAULT_INTERVALS_MIN,
    speed_kmh: float = DEFAULT_SPEED_KMH,
) -> dict[str, Any]:
    """Return a GeoJSON ``FeatureCollection`` — one feature per interval.

    Intervals are emitted in ascending order so the client can stack the
    polygons from largest to smallest if needed (outer band = longest walk).
    """
    if not intervals_min:
        raise ValueError("intervals_min cannot be empty")
    sorted_intervals = sorted({int(m) for m in intervals_min})
    features = [
        compute_walking_isochrone(lat, lon, minutes, speed_kmh=speed_kmh)
        for minutes in sorted_intervals
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "origin": [lon, lat],
            "speedKmh": speed_kmh,
            "intervalsMin": sorted_intervals,
            "approximation": "haversine-circle",
            "note": (
                "Circular Haversine approximation — Module 17 will replace "
                "this with routed OSRM/Valhalla isochrones."
            ),
        },
    }
