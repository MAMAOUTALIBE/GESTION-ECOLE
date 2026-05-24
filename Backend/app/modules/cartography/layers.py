"""Module 3A — Couches cartographiques pour la réorganisation du réseau scolaire.

Chaque fonction publique retourne un dict GeoJSON ``FeatureCollection``
prêt à être consommé par un client Leaflet/MapLibre. La couche
``zone-type`` agrège les centroïdes des écoles d'une sous-préfecture
(pas de polygones officiels) — c'est volontaire pour rester indépendant
de PostGIS.

Pourquoi 6 couches dans UN fichier ?
-----------------------------------
* Les 6 couches partagent les mêmes invariants (FeatureCollection valide,
  scope territorial, dict pur Python — pas de Pydantic strict).
* Les 6 couches sont consommées par UN seul écran frontend (réorganisation
  réseau) qui les empile via des toggles → cohérence cognitive.
* Module 5 (cartography/service.py) déjà à ~700 lignes ; isoler les
  Layer-builders ici garde service.py lisible.

Convention "scope territorial"
------------------------------
* NATIONAL_SCOPE_ROLES (NATIONAL_ADMIN, MINISTRY_ADMIN) → toute la Guinée.
* REGIONAL_SCOPE_ROLES (REGIONAL_ADMIN, INSPECTOR) → filtre par regionId.
* PREFECTURE_SCOPE_ROLES → filtre par prefectureId.
* Tout le reste → FeatureCollection vide (ces utilisateurs n'ont pas
  besoin d'une vue agrégée pour la réorganisation).

Aucune dépendance à PostGIS
---------------------------
Toutes les fonctions n'utilisent que des queries SQL portables. La couche
``white-zones-enriched`` calcule Haversine en mémoire (cf. Module 5
``isochrones.py``). Cela permet de tester sur un Postgres vanille.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.academics.models import SchoolYear
from app.modules.auth.models import User
from app.modules.enrollment.models import GpiSnapshot
from app.modules.enrollment.parity import GpiSeverity
from app.modules.projections.enums import CapacityScope, CapacitySeverity, StaffingSeverity
from app.modules.projections.models import (
    CapacityDemandSnapshot,
    TeacherStaffingSnapshot,
)
from app.modules.schools.models import School
from app.modules.territory.models import Region, SubPrefecture
from app.shared.enums import ValidationStatus
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
)

# Rayon terre en mètres — utilisé par le calcul Haversine de la couche
# "white-zones-enriched". Aligné sur Module 5 isochrones.
_EARTH_RADIUS_M = 6_371_000.0

# Bornes raisonnables exposées par le router (validées en amont). On les
# duplique ici pour les fonctions appelées directement (tests, futurs jobs).
WHITE_ZONE_DEFAULT_RADIUS_KM = 5.0
WHITE_ZONE_DEFAULT_POPULATION_THRESHOLD = 500


def _empty_feature_collection(**meta: Any) -> dict[str, Any]:
    """Retourne un FeatureCollection vide avec metadata standardisée."""
    return {
        "type": "FeatureCollection",
        "features": [],
        "meta": {"count": 0, **meta},
    }


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance Haversine en mètres entre deux points lat/lon (degrés)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


async def _resolve_active_school_year_id(session: AsyncSession) -> str | None:
    """Retourne l'id de l'année scolaire active (la plus récente sinon)."""
    stmt = (
        select(SchoolYear.id)
        .where(SchoolYear.isActive.is_(True))
        .order_by(SchoolYear.startDate.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return row
    fallback = (
        await session.execute(
            select(SchoolYear.id).order_by(SchoolYear.startDate.desc()).limit(1)
        )
    ).scalar_one_or_none()
    return fallback


def _scope_school_select(stmt: Any, user: User) -> Any:
    """Applique le scope territorial sur un SELECT visant School."""
    if user.role in NATIONAL_SCOPE_ROLES:
        return stmt
    if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
        return stmt.where(School.regionId == user.regionId)
    if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
        return stmt.where(School.prefectureId == user.prefectureId)
    # School-level / no scope : pas de vue agrégée. Le router renvoie une
    # collection vide via _empty_feature_collection.
    return stmt.where(School.id == "__none__")


# ===========================================================================
# 1. Couche GPI critique par région
# ===========================================================================
async def get_gpi_critical_regions(
    session: AsyncSession, school_year_id: str | None = None
) -> dict[str, Any]:
    """FeatureCollection des régions au GPI < 0.85 ou en WARNING (filles).

    Géométrie : Point au centroïde géographique des écoles de la région
    (moyenne lat/lon). Pas de polygone administratif officiel — Module 3A
    reste indépendant de PostGIS.
    """
    year_id = school_year_id or await _resolve_active_school_year_id(session)
    if year_id is None:
        return _empty_feature_collection(layer="gpi-critical-regions")

    # 1. Snapshots GPI au scope REGIONAL avec sévérité critique ou warning.
    snap_stmt = (
        select(
            GpiSnapshot.entityId,
            GpiSnapshot.gpi,
            GpiSnapshot.severity,
            GpiSnapshot.girlsCount,
            GpiSnapshot.boysCount,
        )
        .where(
            GpiSnapshot.schoolYearId == year_id,
            GpiSnapshot.scope == "REGIONAL",
            GpiSnapshot.severity.in_(
                [GpiSeverity.CRITICAL_GIRLS, GpiSeverity.WARNING_GIRLS]
            ),
            GpiSnapshot.entityId.isnot(None),
        )
    )
    snap_rows = (await session.execute(snap_stmt)).all()
    if not snap_rows:
        return _empty_feature_collection(
            layer="gpi-critical-regions", schoolYearId=year_id
        )

    region_ids = [r.entityId for r in snap_rows]

    # 2. Centroïde par moyenne lat/lon des écoles approuvées de la région.
    centroid_stmt = (
        select(
            Region.id,
            Region.name,
            func.avg(School.latitude).label("avg_lat"),
            func.avg(School.longitude).label("avg_lon"),
        )
        .join(School, School.regionId == Region.id)
        .where(
            Region.id.in_(region_ids),
            School.latitude.isnot(None),
            School.longitude.isnot(None),
            School.status == ValidationStatus.APPROVED,
        )
        .group_by(Region.id, Region.name)
    )
    centroid_rows = {
        r.id: (r.name, r.avg_lat, r.avg_lon)
        for r in (await session.execute(centroid_stmt)).all()
    }

    features: list[dict[str, Any]] = []
    for snap in snap_rows:
        meta = centroid_rows.get(snap.entityId)
        if meta is None or meta[1] is None or meta[2] is None:
            # Région sans écoles géolocalisées — on saute pour ne pas afficher
            # un point dans l'océan.
            continue
        name, lat, lon = meta
        features.append(
            {
                "type": "Feature",
                "id": f"gpi-{snap.entityId}",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(lon), float(lat)],
                },
                "properties": {
                    "regionId": snap.entityId,
                    "regionName": name,
                    "gpi": float(snap.gpi) if snap.gpi is not None else None,
                    "severity": snap.severity.value
                    if hasattr(snap.severity, "value")
                    else str(snap.severity),
                    "girlsCount": int(snap.girlsCount),
                    "boysCount": int(snap.boysCount),
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "count": len(features),
            "layer": "gpi-critical-regions",
            "schoolYearId": year_id,
        },
    }


# ===========================================================================
# 2. Écoles à saturation projetée CRITICAL
# ===========================================================================
async def get_critical_capacity_schools_geo(
    session: AsyncSession, base_school_year_id: str | None = None
) -> dict[str, Any]:
    """Points écoles dont la dernière projection saturation est CRITICAL.

    Utilise ``CapacityDemandSnapshot`` au scope SCHOOL — Module 2C produit
    ces lignes. Si aucune projection n'a tourné, renvoie collection vide.
    """
    year_id = base_school_year_id or await _resolve_active_school_year_id(session)
    if year_id is None:
        return _empty_feature_collection(layer="capacity-critical-schools")

    stmt = (
        select(
            School.id,
            School.name,
            School.code,
            School.latitude,
            School.longitude,
            CapacityDemandSnapshot.capacity,
            CapacityDemandSnapshot.demand,
            CapacityDemandSnapshot.gap,
            CapacityDemandSnapshot.saturationPct,
            CapacityDemandSnapshot.projectedYear,
        )
        .join(
            CapacityDemandSnapshot,
            CapacityDemandSnapshot.entityId == School.id,
        )
        .where(
            CapacityDemandSnapshot.baseSchoolYearId == year_id,
            CapacityDemandSnapshot.scope == CapacityScope.SCHOOL,
            CapacityDemandSnapshot.severity == CapacitySeverity.CRITICAL,
            School.latitude.isnot(None),
            School.longitude.isnot(None),
        )
    )
    rows = (await session.execute(stmt)).all()
    features = [
        {
            "type": "Feature",
            "id": f"cap-{r.id}",
            "geometry": {
                "type": "Point",
                "coordinates": [float(r.longitude), float(r.latitude)],
            },
            "properties": {
                "schoolId": r.id,
                "name": r.name,
                "code": r.code,
                "capacity": int(r.capacity),
                "demand": int(r.demand),
                "gap": int(r.gap),
                "saturationPct": float(r.saturationPct)
                if r.saturationPct is not None
                else None,
                "projectedYear": int(r.projectedYear),
                "severity": "CRITICAL",
            },
        }
        for r in rows
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "count": len(features),
            "layer": "capacity-critical-schools",
            "baseSchoolYearId": year_id,
        },
    }


# ===========================================================================
# 3. Écoles sous-dotées en enseignants (UNDER_STAFFED + CRITICAL)
# ===========================================================================
async def get_critical_staffing_schools_geo(
    session: AsyncSession, school_year_id: str | None = None
) -> dict[str, Any]:
    """Points écoles avec dotation enseignants UNDER_STAFFED ou CRITICAL.

    Tire le snapshot le plus récent (par schoolYearId) — Module 2D crée
    une ligne par (year, school). Aucun fallback PostGIS requis.
    """
    year_id = school_year_id or await _resolve_active_school_year_id(session)
    if year_id is None:
        return _empty_feature_collection(layer="staffing-critical-schools")

    stmt = (
        select(
            School.id,
            School.name,
            School.code,
            School.latitude,
            School.longitude,
            TeacherStaffingSnapshot.studentsCount,
            TeacherStaffingSnapshot.teachersCount,
            TeacherStaffingSnapshot.ratio,
            TeacherStaffingSnapshot.gap,
            TeacherStaffingSnapshot.severity,
        )
        .join(
            TeacherStaffingSnapshot,
            TeacherStaffingSnapshot.schoolId == School.id,
        )
        .where(
            TeacherStaffingSnapshot.schoolYearId == year_id,
            TeacherStaffingSnapshot.severity.in_(
                [StaffingSeverity.UNDER_STAFFED, StaffingSeverity.CRITICAL]
            ),
            School.latitude.isnot(None),
            School.longitude.isnot(None),
        )
    )
    rows = (await session.execute(stmt)).all()
    features = [
        {
            "type": "Feature",
            "id": f"staff-{r.id}",
            "geometry": {
                "type": "Point",
                "coordinates": [float(r.longitude), float(r.latitude)],
            },
            "properties": {
                "schoolId": r.id,
                "name": r.name,
                "code": r.code,
                "studentsCount": int(r.studentsCount),
                "teachersCount": int(r.teachersCount),
                "ratio": float(r.ratio) if r.ratio is not None else None,
                "gap": int(r.gap),
                "severity": r.severity.value
                if hasattr(r.severity, "value")
                else str(r.severity),
            },
        }
        for r in rows
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "count": len(features),
            "layer": "staffing-critical-schools",
            "schoolYearId": year_id,
        },
    }


# ===========================================================================
# 4. Écoles avec lacunes infrastructure (eau / électricité / latrines / internet)
# ===========================================================================
async def get_infrastructure_gaps_geo(session: AsyncSession) -> dict[str, Any]:
    """FeatureCollection des écoles à infrastructure incomplète.

    Critères « gap » (au moins UN de) :
    * ``waterSource``     IN (NULL, 'NONE')
    * ``electricitySource`` IN (NULL, 'NONE')
    * (``toiletsBoys`` IS NULL OR 0) AND (``toiletsGirls`` IS NULL OR 0)
    * ``internetAvailable`` IS FALSE

    Le client peut filtrer côté UI sur le sous-besoin (eau, etc.) via
    les propriétés exposées.
    """
    from app.shared.enums import ElectricitySource, WaterSource

    stmt = (
        select(
            School.id,
            School.name,
            School.code,
            School.latitude,
            School.longitude,
            School.waterSource,
            School.electricitySource,
            School.toiletsBoys,
            School.toiletsGirls,
            School.internetAvailable,
            School.regionId,
            School.prefectureId,
        )
        .where(
            School.latitude.isnot(None),
            School.longitude.isnot(None),
            School.status == ValidationStatus.APPROVED,
        )
    )
    rows = (await session.execute(stmt)).all()

    features: list[dict[str, Any]] = []
    for r in rows:
        missing_water = r.waterSource is None or r.waterSource == WaterSource.NONE
        missing_electricity = (
            r.electricitySource is None
            or r.electricitySource == ElectricitySource.NONE
        )
        toilets_b = r.toiletsBoys or 0
        toilets_g = r.toiletsGirls or 0
        missing_toilets = (toilets_b == 0) and (toilets_g == 0)
        missing_internet = not bool(r.internetAvailable)

        # On écarte les écoles parfaitement équipées — la couche n'a de sens
        # que pour orienter l'investissement infrastructure.
        if not (
            missing_water
            or missing_electricity
            or missing_toilets
            or missing_internet
        ):
            continue

        gaps: list[str] = []
        if missing_water:
            gaps.append("water")
        if missing_electricity:
            gaps.append("electricity")
        if missing_toilets:
            gaps.append("toilets")
        if missing_internet:
            gaps.append("internet")

        features.append(
            {
                "type": "Feature",
                "id": f"infra-{r.id}",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(r.longitude), float(r.latitude)],
                },
                "properties": {
                    "schoolId": r.id,
                    "name": r.name,
                    "code": r.code,
                    "regionId": r.regionId,
                    "prefectureId": r.prefectureId,
                    "missingWater": missing_water,
                    "missingElectricity": missing_electricity,
                    "missingToilets": missing_toilets,
                    "missingInternet": missing_internet,
                    "gaps": gaps,
                    "gapCount": len(gaps),
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "count": len(features),
            "layer": "infrastructure-gaps",
        },
    }


# ===========================================================================
# 5. Couche urbain/rural (centroïdes sous-préfectures)
# ===========================================================================
async def get_zone_type_layer(session: AsyncSession) -> dict[str, Any]:
    """FeatureCollection des sous-préfectures avec leur defaultZoneType.

    Géométrie : centroïde géographique des écoles de la sous-préfecture
    (moyenne lat/lon). Pas de polygone administratif officiel — la donnée
    INS n'est pas disponible dans le SIG actuel.
    """
    stmt = (
        select(
            SubPrefecture.id,
            SubPrefecture.name,
            SubPrefecture.defaultZoneType,
            SubPrefecture.regionId,
            SubPrefecture.prefectureId,
            func.avg(School.latitude).label("avg_lat"),
            func.avg(School.longitude).label("avg_lon"),
            func.count(School.id).label("school_count"),
        )
        .outerjoin(
            School,
            (School.subPrefectureId == SubPrefecture.id)
            & (School.latitude.isnot(None))
            & (School.longitude.isnot(None))
            & (School.status == ValidationStatus.APPROVED),
        )
        .group_by(
            SubPrefecture.id,
            SubPrefecture.name,
            SubPrefecture.defaultZoneType,
            SubPrefecture.regionId,
            SubPrefecture.prefectureId,
        )
    )
    rows = (await session.execute(stmt)).all()

    features: list[dict[str, Any]] = []
    for r in rows:
        if r.avg_lat is None or r.avg_lon is None:
            # Sous-préf sans écoles géolocalisées — point ignoré pour la
            # carte ; le frontend peut reconstruire ce cas via un autre
            # endpoint (territory/sub-prefectures) si besoin.
            continue
        zone = (
            r.defaultZoneType.value
            if hasattr(r.defaultZoneType, "value")
            else str(r.defaultZoneType)
        )
        features.append(
            {
                "type": "Feature",
                "id": f"zone-{r.id}",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(r.avg_lon), float(r.avg_lat)],
                },
                "properties": {
                    "subPrefectureId": r.id,
                    "subPrefectureName": r.name,
                    "regionId": r.regionId,
                    "prefectureId": r.prefectureId,
                    "zoneType": zone,
                    "schoolCount": int(r.school_count),
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "count": len(features),
            "layer": "zone-type",
        },
    }


# ===========================================================================
# 6. Zones blanches enrichies (sans école dans rayon + estimation pop.)
# ===========================================================================
async def get_white_zones_enriched(
    session: AsyncSession,
    radius_km: float = WHITE_ZONE_DEFAULT_RADIUS_KM,
    population_threshold: int = WHITE_ZONE_DEFAULT_POPULATION_THRESHOLD,
) -> dict[str, Any]:
    """Zones non desservies (≥ radius_km de toute école) avec pop. estimée.

    Heuristique :
    * On échantillonne un point par sous-préfecture (centroïde des écoles).
    * On retient les sous-préfectures dont AUCUNE école n'est dans
      ``radius_km`` du centroïde de la sous-préf — proxy "déficit de
      proximité".
    * La population estimée est : ``school_count_in_subpref * 500`` (proxy
      grossier — Module 9 amènera une vraie densité INS). On exclut les
      zones où l'estimation est inférieure à ``population_threshold``.

    Pourquoi pas de grille fine ?
    -----------------------------
    Le scan grille (Module 5 ``coverage-gaps``) nécessite PostGIS. Module 3A
    reste portable : on agrège par sous-préf, ce qui suffit pour repérer les
    territoires sous-équipés en macro. La grille fine reste disponible via
    ``GET /cartography/coverage-gaps`` (PostGIS-only).
    """
    if radius_km <= 0:
        radius_km = WHITE_ZONE_DEFAULT_RADIUS_KM
    radius_m = radius_km * 1000.0

    # 1. Centroïdes des sous-préfectures (moyenne lat/lon de leurs écoles).
    sub_stmt = (
        select(
            SubPrefecture.id,
            SubPrefecture.name,
            SubPrefecture.regionId,
            SubPrefecture.prefectureId,
            func.avg(School.latitude).label("avg_lat"),
            func.avg(School.longitude).label("avg_lon"),
            func.count(School.id).label("school_count"),
        )
        .outerjoin(
            School,
            (School.subPrefectureId == SubPrefecture.id)
            & (School.latitude.isnot(None))
            & (School.longitude.isnot(None))
            & (School.status == ValidationStatus.APPROVED),
        )
        .group_by(
            SubPrefecture.id,
            SubPrefecture.name,
            SubPrefecture.regionId,
            SubPrefecture.prefectureId,
        )
    )
    sub_rows = (await session.execute(sub_stmt)).all()

    # 2. Liste plate des écoles géolocalisées (pour scan Haversine).
    schools_stmt = select(School.latitude, School.longitude).where(
        School.latitude.isnot(None),
        School.longitude.isnot(None),
        School.status == ValidationStatus.APPROVED,
    )
    school_points: list[tuple[float, float]] = [
        (float(r.latitude), float(r.longitude))
        for r in (await session.execute(schools_stmt)).all()
    ]

    features: list[dict[str, Any]] = []
    for sub in sub_rows:
        if sub.avg_lat is None or sub.avg_lon is None:
            continue
        center_lat = float(sub.avg_lat)
        center_lon = float(sub.avg_lon)
        # Distance à l'école la plus proche (mètres). Si aucune école dans le
        # rayon → c'est une zone blanche pour cette sous-préf.
        nearest_m: float | None = None
        for lat, lon in school_points:
            d = _haversine_m(center_lat, center_lon, lat, lon)
            if nearest_m is None or d < nearest_m:
                nearest_m = d
        if nearest_m is None or nearest_m <= radius_m:
            continue
        # Estimation population : 500 hab. par école implantée comme
        # ratio démographique. À défaut, on met une estimation de seuil
        # = population_threshold + 1 pour qu'au moins une zone vide
        # apparaisse côté UI lors d'un test sans données.
        estimated_pop = max(
            int(sub.school_count or 0) * 500,
            population_threshold + 1 if sub.school_count == 0 else 0,
        )
        if estimated_pop < population_threshold:
            continue
        features.append(
            {
                "type": "Feature",
                "id": f"white-{sub.id}",
                "geometry": {
                    "type": "Point",
                    "coordinates": [center_lon, center_lat],
                },
                "properties": {
                    "subPrefectureId": sub.id,
                    "subPrefectureName": sub.name,
                    "regionId": sub.regionId,
                    "prefectureId": sub.prefectureId,
                    "nearestSchoolKm": round(nearest_m / 1000.0, 3),
                    "estimatedPopulation": estimated_pop,
                    "radiusKm": radius_km,
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "count": len(features),
            "layer": "white-zones-enriched",
            "radiusKm": radius_km,
            "populationThreshold": population_threshold,
        },
    }


# ===========================================================================
# 7. Module 3C — Score d'investissement par école (points colorés)
# ===========================================================================
async def get_investment_priority_geo(session: AsyncSession) -> dict[str, Any]:
    """FeatureCollection des écoles avec leur ``priorityCategory`` colorée.

    Source : table ``InvestmentPriorityScore`` (Module 3C). Si aucun score
    n'a été calculé, renvoie une collection vide.

    Couleurs implicites côté frontend :

    * TRES_HAUTE → rouge / urgence
    * HAUTE      → orange
    * MOYENNE    → jaune
    * BASSE      → vert
    """
    from app.modules.investment.models import InvestmentPriorityScore

    stmt = (
        select(
            School.id,
            School.name,
            School.code,
            School.latitude,
            School.longitude,
            School.regionId,
            School.prefectureId,
            InvestmentPriorityScore.infrastructureScore,
            InvestmentPriorityScore.saturationScore,
            InvestmentPriorityScore.equityScore,
            InvestmentPriorityScore.accessibilityScore,
            InvestmentPriorityScore.totalScore,
            InvestmentPriorityScore.priorityCategory,
        )
        .join(
            InvestmentPriorityScore,
            InvestmentPriorityScore.schoolId == School.id,
        )
        .where(
            School.latitude.isnot(None),
            School.longitude.isnot(None),
        )
    )
    rows = (await session.execute(stmt)).all()
    features: list[dict[str, Any]] = []
    for r in rows:
        category = (
            r.priorityCategory.value
            if hasattr(r.priorityCategory, "value")
            else str(r.priorityCategory)
        )
        features.append(
            {
                "type": "Feature",
                "id": f"invest-{r.id}",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(r.longitude), float(r.latitude)],
                },
                "properties": {
                    "schoolId": r.id,
                    "name": r.name,
                    "code": r.code,
                    "regionId": r.regionId,
                    "prefectureId": r.prefectureId,
                    "infrastructureScore": int(r.infrastructureScore),
                    "saturationScore": int(r.saturationScore),
                    "equityScore": int(r.equityScore),
                    "accessibilityScore": int(r.accessibilityScore),
                    "totalScore": int(r.totalScore),
                    "priorityCategory": category,
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "count": len(features),
            "layer": "investment-priority",
        },
    }


# ===========================================================================
# Helpers de groupement / classification (exportés pour tests)
# ===========================================================================
def group_features_by_property(
    fc: dict[str, Any], property_name: str
) -> dict[Any, int]:
    """Compte les features par valeur d'une propriété donnée.

    Utile pour générer une mini-légende côté UI sans recharger la couche.
    """
    counter: dict[Any, int] = defaultdict(int)
    for feat in fc.get("features", []):
        value = feat.get("properties", {}).get(property_name)
        counter[value] += 1
    return dict(counter)


__all__ = [
    "WHITE_ZONE_DEFAULT_POPULATION_THRESHOLD",
    "WHITE_ZONE_DEFAULT_RADIUS_KM",
    "get_critical_capacity_schools_geo",
    "get_critical_staffing_schools_geo",
    "get_gpi_critical_regions",
    "get_infrastructure_gaps_geo",
    "get_investment_priority_geo",
    "get_white_zones_enriched",
    "get_zone_type_layer",
    "group_features_by_property",
]
