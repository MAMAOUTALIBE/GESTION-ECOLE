"""Cartography service — PostGIS-backed spatial queries for the school map.

All public methods accept the authenticated `User` and apply the same
territorial scope filtering as the schools/census modules.
"""
import base64
import contextlib
import hashlib
import json
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PostgisUnavailableError
from app.core.redis import get_redis
from app.modules.auth.models import User
from app.modules.cartography import layers as cartography_layers
from app.modules.cartography.schemas import (
    CatchmentsQuery,
    CoverageGapsQuery,
    DensityFeature,
    DensityResponse,
    Feature,
    FeatureCollection,
    IndicatorsResponse,
    NearbyQuery,
    NearbyResponse,
    NearbySchool,
    RegionDistanceResponse,
    RegionDistanceStat,
    SchoolsGeoQuery,
    TerritoryIndicator,
)
from app.modules.cartography.tiles import generate_mvt
from app.modules.census.models import Student, Teacher
from app.modules.schools.models import School
from app.modules.territory.models import Prefecture, Region, SubPrefecture
from app.shared.enums import ValidationStatus
from app.shared.permissions import (
    NATIONAL_SCOPE_ROLES,
    PREFECTURE_SCOPE_ROLES,
    REGIONAL_SCOPE_ROLES,
    SUB_PREFECTURE_SCOPE_ROLES,
)

# MVT cache: keep tiles in Redis for an hour. Schools rarely move; the
# trade-off is invalidation simplicity vs freshness. Operators flush the
# `mvt:*` namespace manually after a bulk import.
TILE_CACHE_TTL_SECONDS = 3600
TILE_CACHE_PREFIX = "mvt"

# Module 3A — Réorganisation réseau : couches GeoJSON.
# Cache 5 min : les snapshots GPI / capacity / staffing sont recalculés
# manuellement par les admins, donc une fraîcheur < 5 min est largement
# suffisante pour limiter la charge SQL sur des dashboards très consultés.
LAYER_CACHE_TTL_SECONDS = 300
LAYER_CACHE_PREFIX = "cartography:layer"

# Liste exhaustive des couches exposées par Module 3A. Permet au service
# de rejeter immédiatement un nom de couche inconnu (HTTP 404).
SUPPORTED_LAYERS: frozenset[str] = frozenset(
    {
        "gpi-critical-regions",
        "capacity-critical-schools",
        "staffing-critical-schools",
        "infrastructure-gaps",
        "zone-type",
        "white-zones-enriched",
        "investment-priority",
    }
)


class CartographyService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # GET /api/cartography/schools — GeoJSON FeatureCollection
    # ==================================================================
    async def schools_geojson(
        self, user: User, query: SchoolsGeoQuery
    ) -> FeatureCollection:
        stmt = select(
            School.id, School.name, School.code, School.regionId,
            School.prefectureId, School.subPrefectureId,
            School.prefecture, School.commune, School.type,
            School.latitude, School.longitude, School.status,
        ).where(
            School.latitude.isnot(None),
            School.longitude.isnot(None),
        )
        stmt = self._scope_school_query(stmt, user)
        if query.onlyApproved:
            stmt = stmt.where(School.status == ValidationStatus.APPROVED)
        if query.regionId:
            stmt = stmt.where(School.regionId == query.regionId)
        if query.prefectureId:
            stmt = stmt.where(School.prefectureId == query.prefectureId)
        if query.subPrefectureId:
            stmt = stmt.where(School.subPrefectureId == query.subPrefectureId)

        rows = (await self.session.execute(stmt)).all()
        features = [
            Feature(
                id=r.id,
                geometry={"type": "Point", "coordinates": (r.longitude, r.latitude)},
                properties={
                    "name": r.name,
                    "code": r.code,
                    "regionId": r.regionId,
                    "prefectureId": r.prefectureId,
                    "subPrefectureId": r.subPrefectureId,
                    "prefecture": r.prefecture,
                    "commune": r.commune,
                    "type": r.type,
                    "status": r.status.value,
                },
            )
            for r in rows
        ]
        return FeatureCollection(features=features, meta={"count": len(features)})

    # ==================================================================
    # GET /api/cartography/schools/nearby
    # ==================================================================
    async def schools_nearby(self, user: User, query: NearbyQuery) -> NearbyResponse:
        # ST_DWithin on geography uses meters
        radius_m = query.radiusKm * 1000
        origin = func.ST_SetSRID(
            func.ST_MakePoint(query.lng, query.lat), 4326
        ).cast(text("geography"))

        # Distance using ST_Distance on geography returns meters.
        distance_expr = func.ST_Distance(School.geom, origin).label("distance_m")

        stmt = (
            select(
                School.id, School.name, School.code,
                School.latitude, School.longitude,
                distance_expr,
            )
            .where(
                School.geom.isnot(None),
                func.ST_DWithin(School.geom, origin, radius_m),
                School.status == ValidationStatus.APPROVED,
            )
            .order_by(distance_expr)
            .limit(query.limit)
        )
        stmt = self._scope_school_query(stmt, user)

        rows = (await self.session.execute(stmt)).all()
        if not rows:
            return NearbyResponse(
                origin=(query.lat, query.lng), radiusKm=query.radiusKm, items=[]
            )

        # Augment with student / teacher counts in a single batched query
        ids = [r.id for r in rows]
        students = await self._counts_by(Student.schoolId, Student, ids)
        teachers = await self._counts_by(Teacher.schoolId, Teacher, ids)

        items = [
            NearbySchool(
                id=r.id, name=r.name, code=r.code,
                latitude=r.latitude, longitude=r.longitude,
                distanceKm=round(r.distance_m / 1000, 3),
                students=students.get(r.id, 0),
                teachers=teachers.get(r.id, 0),
            )
            for r in rows
        ]
        return NearbyResponse(
            origin=(query.lat, query.lng), radiusKm=query.radiusKm, items=items
        )

    # ==================================================================
    # GET /api/cartography/catchments — Voronoi polygons
    # ==================================================================
    async def voronoi_catchments(
        self, user: User, query: CatchmentsQuery
    ) -> FeatureCollection:
        """Voronoi catchment polygons assigning every land area to its
        nearest school. Computed entirely server-side via PostGIS.
        """
        # NOTE: territorial RBAC is enforced by adding regionId clauses to
        # the inner CTE. Users without national scope must specify a region.
        scope_clause = "geom IS NOT NULL AND status = 'APPROVED'"
        params: dict[str, Any] = {}
        if user.role not in NATIONAL_SCOPE_ROLES:
            if user.regionId:
                scope_clause += ' AND "regionId" = :user_region'
                params["user_region"] = user.regionId
            elif user.schoolId:
                scope_clause += ' AND id = :user_school'
                params["user_school"] = user.schoolId
            else:
                return FeatureCollection(features=[], meta={"count": 0})
        if query.regionId:
            scope_clause += ' AND "regionId" = :rid'
            params["rid"] = query.regionId
        if query.prefectureId:
            scope_clause += ' AND "prefectureId" = :pid'
            params["pid"] = query.prefectureId

        sql = text(
            f"""
            WITH school_scope AS (
                SELECT id, geom FROM "School" WHERE {scope_clause}
            ),
            points AS (
                SELECT id, geom::geometry AS geom FROM school_scope
            ),
            multi AS (
                SELECT ST_Collect(geom) AS mp FROM points
            ),
            vor AS (
                SELECT (ST_Dump(ST_VoronoiPolygons(mp, 0))).geom AS poly FROM multi
            )
            SELECT p.id AS school_id, ST_AsGeoJSON(v.poly) AS geojson
            FROM vor v
            JOIN points p ON ST_Within(p.geom, v.poly);
            """
        )
        rows = (await self.session.execute(sql, params)).all()

        import json  # noqa: PLC0415

        features = [
            Feature(
                id=row.school_id,
                geometry=json.loads(row.geojson),
                properties={"schoolId": row.school_id},
            )
            for row in rows
        ]
        return FeatureCollection(features=features, meta={"count": len(features)})

    # ==================================================================
    # GET /api/cartography/coverage-gaps
    # ==================================================================
    async def coverage_gaps(
        self, user: User, query: CoverageGapsQuery
    ) -> FeatureCollection:
        """Detect grid cells that have no school within `radiusKm`.

        The grid is built within the bounding box of in-scope schools (or
        the requested region). Points without any nearby school are returned
        as a FeatureCollection of Points + a `radiusKm` halo property so the
        client can render circles.
        """
        radius_m = query.radiusKm * 1000
        step_deg = query.gridStepKm / 111.0  # rough degrees per km at equator

        # Compute bounding box on the fly via PostGIS
        scope_clause = "geom IS NOT NULL AND status = 'APPROVED'"
        params: dict[str, Any] = {"step_deg": step_deg, "radius_m": radius_m}
        if query.regionId:
            scope_clause += ' AND "regionId" = :rid'
            params["rid"] = query.regionId

        sql = text(
            f"""
            WITH bbox AS (
                SELECT
                    MIN(ST_X(geom::geometry)) AS minx,
                    MIN(ST_Y(geom::geometry)) AS miny,
                    MAX(ST_X(geom::geometry)) AS maxx,
                    MAX(ST_Y(geom::geometry)) AS maxy
                FROM "School"
                WHERE {scope_clause}
            ),
            grid AS (
                SELECT generate_series(b.minx, b.maxx, :step_deg) AS x, b.* FROM bbox b
            ),
            cells AS (
                SELECT
                    g.x AS x,
                    generate_series(g.miny, g.maxy, :step_deg) AS y
                FROM grid g
            ),
            uncovered AS (
                SELECT c.x, c.y
                FROM cells c
                WHERE NOT EXISTS (
                    SELECT 1 FROM "School" s
                    WHERE {scope_clause}
                      AND ST_DWithin(
                            s.geom,
                            ST_SetSRID(ST_MakePoint(c.x, c.y), 4326)::geography,
                            :radius_m
                          )
                )
            )
            SELECT x, y FROM uncovered;
            """
        )
        rows = (await self.session.execute(sql, params)).all()
        features = [
            Feature(
                id=f"gap-{i}",
                geometry={"type": "Point", "coordinates": (row.x, row.y)},
                properties={
                    "kind": "coverage_gap",
                    "radiusKm": query.radiusKm,
                },
            )
            for i, row in enumerate(rows)
        ]
        return FeatureCollection(
            features=features,
            meta={"count": len(features), "radiusKm": query.radiusKm,
                  "gridStepKm": query.gridStepKm},
        )

    # ==================================================================
    # GET /api/cartography/indicators
    # ==================================================================
    async def indicators(self, user: User, level: str) -> IndicatorsResponse:
        # Group schools by territory level and compute spatial KPIs
        # avg distance to nearest sibling school (within the same territory)
        if level == "region":
            territory_table, parent_table = Region, None
            territory_col = School.regionId
        elif level == "prefecture":
            territory_table, parent_table = Prefecture, Region
            territory_col = School.prefectureId
        elif level == "subPrefecture":
            territory_table, parent_table = SubPrefecture, Prefecture
            territory_col = School.subPrefectureId
        else:
            raise NotFoundError(detail=f"Unsupported indicators level: {level}")

        # Schools per territory
        school_stmt = self._scope_school_query(
            select(
                territory_col.label("tid"), School.id, School.geom,
                School.latitude, School.longitude,
            ),
            user,
        ).where(territory_col.isnot(None), School.status == ValidationStatus.APPROVED)
        school_rows = (await self.session.execute(school_stmt)).all()

        # Group in-process — for 50K+ schools this stays fast since we only carry IDs
        from collections import defaultdict  # noqa: PLC0415

        by_territory: dict[str, list[Any]] = defaultdict(list)
        for r in school_rows:
            by_territory[r.tid].append(r)
        territory_ids = list(by_territory.keys())

        # Counts: students/teachers per school
        school_ids = [s.id for r in by_territory.values() for s in r]
        students = await self._counts_by(Student.schoolId, Student, school_ids)
        teachers = await self._counts_by(Teacher.schoolId, Teacher, school_ids)

        # Average inter-school distance per territory (nearest neighbour mean)
        avg_distance: dict[str, float] = {}
        if territory_ids:
            sql = text(
                f"""
                WITH scoped AS (
                    SELECT "{territory_col.name}" AS tid, id, geom
                    FROM "School"
                    WHERE "{territory_col.name}" = ANY(:tids)
                      AND geom IS NOT NULL
                      AND status = 'APPROVED'
                ),
                nearest AS (
                    SELECT a.tid AS tid, a.id AS sid,
                           MIN(ST_Distance(a.geom, b.geom)) AS d
                    FROM scoped a
                    JOIN scoped b ON a.tid = b.tid AND a.id <> b.id
                    GROUP BY a.tid, a.id
                )
                SELECT tid, AVG(d) AS avg_d FROM nearest GROUP BY tid;
                """
            )
            rows = (
                await self.session.execute(sql, {"tids": territory_ids})
            ).all()
            for row in rows:
                if row.avg_d is not None:
                    avg_distance[row.tid] = round(row.avg_d / 1000, 3)

        # Territory names + parent
        territory_stmt = select(
            territory_table.id, territory_table.name,
            *(
                [parent_table.id.label("parent_id"), parent_table.name.label("parent_name")]
                if parent_table is not None else []
            ),
        )
        if parent_table is not None:
            if parent_table is Region:
                territory_stmt = territory_stmt.join(
                    parent_table, parent_table.id == territory_table.regionId
                )
            elif parent_table is Prefecture:
                territory_stmt = territory_stmt.join(
                    parent_table, parent_table.id == territory_table.prefectureId
                )
        territory_stmt = territory_stmt.where(territory_table.id.in_(territory_ids))
        meta_rows = (await self.session.execute(territory_stmt)).all()
        meta_by_id = {row.id: row for row in meta_rows}

        items: list[TerritoryIndicator] = []
        for tid, schools in by_territory.items():
            geo = sum(1 for s in schools if s.geom is not None)
            t_students = sum(students.get(s.id, 0) for s in schools)
            t_teachers = sum(teachers.get(s.id, 0) for s in schools)
            ratio = round((t_students / t_teachers) * 10) / 10 if t_teachers else 0.0
            coverage = round((geo / len(schools)) * 100) if schools else 0.0
            meta = meta_by_id.get(tid)
            items.append(TerritoryIndicator(
                territoryId=tid,
                territoryName=meta.name if meta else tid,
                parentId=getattr(meta, "parent_id", None) if meta else None,
                parentName=getattr(meta, "parent_name", None) if meta else None,
                schools=len(schools),
                geolocatedSchools=geo,
                students=t_students,
                teachers=t_teachers,
                studentsPerTeacher=ratio,
                avgDistanceToNearestSchoolKm=avg_distance.get(tid),
                coverageRate=coverage,
            ))
        items.sort(key=lambda i: i.students, reverse=True)
        return IndicatorsResponse(level=level, items=items)  # type: ignore[arg-type]

    # ==================================================================
    # Helpers
    # ==================================================================
    def _scope_school_query(self, stmt, user: User):  # type: ignore[no-untyped-def]
        if user.role in NATIONAL_SCOPE_ROLES:
            return stmt
        if user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            return stmt.where(School.regionId == user.regionId)
        if user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            return stmt.where(School.prefectureId == user.prefectureId)
        if user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            return stmt.where(School.subPrefectureId == user.subPrefectureId)
        if user.schoolId:
            return stmt.where(School.id == user.schoolId)
        return stmt.where(School.id == "__none__")

    async def _counts_by(
        self, group_col: Any, model: Any, ids: list[str]
    ) -> dict[str, int]:
        if not ids:
            return {}
        rows = (
            await self.session.execute(
                select(group_col, func.count()).where(group_col.in_(ids)).group_by(group_col)
            )
        ).all()
        return dict(rows)

    # ==================================================================
    # Module 5 — Vector tiles (MVT) with Redis cache
    # ==================================================================
    async def get_tile(self, z: int, x: int, y: int) -> bytes:
        """Return the binary MVT for the requested tile, using Redis as
        a write-through cache (TTL 1h).

        Cache key shape: ``mvt:{z}:{x}:{y}``. Tile bodies are bytes; we
        base64-encode them before storing so the connection uses
        ``decode_responses=True`` safely (Redis stores str values).
        """
        cache_key = self._tile_cache_key(z, x, y)
        redis = get_redis()

        # 1. Cache hit path — short-circuit DB roundtrip.
        cached: str | None = None
        with contextlib.suppress(Exception):  # pragma: no cover - redis transient
            cached = await redis.get(cache_key)
        if cached is not None:
            with contextlib.suppress(Exception):  # corrupt cache → fall through
                return base64.b64decode(cached.encode("ascii"))

        # 2. Miss → compute via PostGIS, then persist.
        tile_bytes = await generate_mvt(self.session, z, x, y)
        with contextlib.suppress(Exception):  # pragma: no cover - redis transient
            await redis.set(
                cache_key,
                base64.b64encode(tile_bytes).decode("ascii"),
                ex=TILE_CACHE_TTL_SECONDS,
            )
        return tile_bytes

    @staticmethod
    def _tile_cache_key(z: int, x: int, y: int) -> str:
        return f"{TILE_CACHE_PREFIX}:{z}:{x}:{y}"

    # ==================================================================
    # Module 5 — Student density by sub-prefecture (choropleth feed)
    # ==================================================================
    async def get_subprefecture_density(self, user: User) -> DensityResponse:
        """Return student density per sub-prefecture (students per km²).

        The area is computed from the convex hull of in-scope schools when
        a sub-prefecture polygon is missing from the database (current state
        — Module 5 doesn't introduce geom columns on territories). The
        ``areaKm2`` field will therefore be ``0.0`` for sub-prefectures with
        fewer than 3 geo-located schools; downstream UI hides those rows.

        Raises
        ------
        PostgisUnavailableError
            Surfaced when ``ST_ConvexHull``/``ST_Area`` are missing.
        """
        scope_clause = ""
        params: dict[str, Any] = {}
        if user.role in NATIONAL_SCOPE_ROLES:
            pass
        elif user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            scope_clause = ' AND sp."regionId" = :user_region'
            params["user_region"] = user.regionId
        elif user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            scope_clause = ' AND sp."prefectureId" = :user_prefecture'
            params["user_prefecture"] = user.prefectureId
        elif user.role in SUB_PREFECTURE_SCOPE_ROLES and user.subPrefectureId:
            scope_clause = ' AND sp.id = :user_subpref'
            params["user_subpref"] = user.subPrefectureId
        else:
            # School-scoped users (TEACHER / DIRECTOR) don't see aggregates.
            return DensityResponse(items=[])

        sql = text(
            f"""
            WITH scoped_schools AS (
                SELECT s.id, s."subPrefectureId", s.geom
                FROM "School" s
                JOIN "SubPrefecture" sp ON sp.id = s."subPrefectureId"
                WHERE s.geom IS NOT NULL
                  AND s.status = 'APPROVED'
                  {scope_clause}
            ),
            student_counts AS (
                SELECT
                    sp.id AS sub_id,
                    sp.name AS sub_name,
                    sp."regionId" AS region_id,
                    sp."prefectureId" AS prefecture_id,
                    COUNT(st.id)::int AS student_count
                FROM "SubPrefecture" sp
                LEFT JOIN "School" s2 ON s2."subPrefectureId" = sp.id
                LEFT JOIN "Student" st ON st."schoolId" = s2.id
                WHERE 1=1 {scope_clause}
                GROUP BY sp.id, sp.name, sp."regionId", sp."prefectureId"
            ),
            hulls AS (
                SELECT
                    "subPrefectureId" AS sub_id,
                    -- ST_Area on geography returns square metres.
                    COALESCE(
                        ST_Area(ST_ConvexHull(ST_Collect(geom::geometry))::geography),
                        0
                    ) / 1e6 AS area_km2
                FROM scoped_schools
                GROUP BY "subPrefectureId"
            )
            SELECT
                sc.sub_id, sc.sub_name, sc.region_id, sc.prefecture_id,
                sc.student_count,
                COALESCE(h.area_km2, 0) AS area_km2
            FROM student_counts sc
            LEFT JOIN hulls h ON h.sub_id = sc.sub_id
            ORDER BY sc.student_count DESC;
            """
        )

        try:
            rows = (await self.session.execute(sql, params)).all()
        except (ProgrammingError, DBAPIError) as exc:
            message = str(exc).lower()
            if (
                "st_convexhull" in message
                or "st_collect" in message
                or "st_area" in message
                or "geography" in message
            ):
                raise PostgisUnavailableError(
                    detail=(
                        "PostGIS is required for choropleth density "
                        "calculations — `CREATE EXTENSION postgis;` first."
                    ),
                ) from exc
            raise

        items: list[DensityFeature] = []
        for r in rows:
            area = float(r.area_km2 or 0)
            count = int(r.student_count or 0)
            density = round(count / area, 3) if area > 0 else 0.0
            items.append(
                DensityFeature(
                    subPrefectureId=r.sub_id,
                    name=r.sub_name,
                    regionId=r.region_id,
                    prefectureId=r.prefecture_id,
                    studentCount=count,
                    areaKm2=round(area, 4),
                    density=density,
                )
            )
        return DensityResponse(items=items)

    # ==================================================================
    # Module 5 — Average school distance per region (PostGIS)
    # ==================================================================
    async def get_region_distance_stats(self, user: User) -> RegionDistanceResponse:
        """For each in-scope region, compute the mean inter-school distance
        (km) — a coarse proxy for "how far does a student walk to school".

        Note: this is the average distance between schools (nearest-neighbour
        per school, averaged), not the average distance a *student* lives
        from their school. The full version requires student home coords,
        which are not in the schema yet (backlog Module 6).
        """
        scope_clause = ""
        params: dict[str, Any] = {}
        if user.role in NATIONAL_SCOPE_ROLES:
            pass
        elif user.role in REGIONAL_SCOPE_ROLES and user.regionId:
            scope_clause = ' AND r.id = :user_region'
            params["user_region"] = user.regionId
        elif user.role in PREFECTURE_SCOPE_ROLES and user.prefectureId:
            # Prefecture admins only see their region (we aggregate at region
            # level — they get one row).
            scope_clause = ' AND r.id IN (SELECT "regionId" FROM "Prefecture" WHERE id = :p)'
            params["p"] = user.prefectureId
        else:
            return RegionDistanceResponse(items=[])

        sql = text(
            f"""
            WITH scoped AS (
                SELECT r.id AS region_id, r.name AS region_name,
                       s.id AS school_id, s.geom
                FROM "Region" r
                JOIN "School" s ON s."regionId" = r.id
                WHERE s.geom IS NOT NULL
                  AND s.status = 'APPROVED'
                  {scope_clause}
            ),
            nearest AS (
                SELECT a.region_id, a.school_id,
                       MIN(ST_Distance(a.geom, b.geom)) AS d_m
                FROM scoped a
                JOIN scoped b
                  ON a.region_id = b.region_id AND a.school_id <> b.school_id
                GROUP BY a.region_id, a.school_id
            ),
            agg AS (
                SELECT region_id, AVG(d_m) AS avg_d_m, COUNT(*) AS school_count
                FROM nearest
                GROUP BY region_id
            ),
            students AS (
                SELECT r.id AS region_id,
                       COUNT(st.id)::int AS student_count
                FROM "Region" r
                LEFT JOIN "School" s ON s."regionId" = r.id
                LEFT JOIN "Student" st ON st."schoolId" = s.id
                WHERE 1=1 {scope_clause.replace('r.id =', 'r.id =') if scope_clause else ''}
                GROUP BY r.id
            )
            SELECT
                r.id AS region_id,
                r.name AS region_name,
                agg.avg_d_m,
                COALESCE(agg.school_count, 0) AS school_count,
                COALESCE(students.student_count, 0) AS student_count
            FROM "Region" r
            LEFT JOIN agg ON agg.region_id = r.id
            LEFT JOIN students ON students.region_id = r.id
            WHERE 1=1 {scope_clause}
            ORDER BY r.name;
            """
        )

        try:
            rows = (await self.session.execute(sql, params)).all()
        except (ProgrammingError, DBAPIError) as exc:
            message = str(exc).lower()
            if "st_distance" in message or "geography" in message:
                raise PostgisUnavailableError(
                    detail=(
                        "PostGIS is required for distance aggregates — "
                        "`CREATE EXTENSION postgis;` first."
                    ),
                ) from exc
            raise

        items: list[RegionDistanceStat] = []
        for r in rows:
            avg_km = round(float(r.avg_d_m) / 1000, 3) if r.avg_d_m else None
            items.append(
                RegionDistanceStat(
                    regionId=r.region_id,
                    regionName=r.region_name,
                    avgSchoolDistanceKm=avg_km,
                    schoolCount=int(r.school_count or 0),
                    studentCount=int(r.student_count or 0),
                )
            )
        return RegionDistanceResponse(items=items)

    # ==================================================================
    # Module 3A — Couches dynamiques pour la réorganisation du réseau
    # ==================================================================
    @staticmethod
    def _layer_cache_key(
        name: str, params: dict[str, Any], scope_user: User
    ) -> str:
        """Dérive une clé Redis stable de (couche, params, scope user).

        Le scope est inclus dans la clé pour éviter qu'un INSPECTOR voie
        des features mises en cache par un MINISTRY_ADMIN (et inversement).
        """
        scope_token = (
            f"{scope_user.role}:{scope_user.regionId or '-'}:"
            f"{scope_user.prefectureId or '-'}"
        )
        # On stringify les params en JSON trié pour éviter qu'une permutation
        # de clé ne crée un miss inutile.
        serialised = json.dumps(params, sort_keys=True, default=str)
        digest = hashlib.sha256(
            f"{scope_token}|{serialised}".encode()
        ).hexdigest()[:16]
        return f"{LAYER_CACHE_PREFIX}:{name}:{digest}"

    async def get_layer(
        self,
        name: str,
        params: dict[str, Any],
        scope_user: User,
    ) -> dict[str, Any]:
        """Dispatch + cache pour les 6 couches Module 3A.

        - 404 si ``name`` n'est pas dans ``SUPPORTED_LAYERS``.
        - Renvoie un FeatureCollection vide pour les utilisateurs sans
          scope agrégé (école / pas de role national/régional).
        - Cache Redis 5 min, sourd aux erreurs transitoires.
        """
        if name not in SUPPORTED_LAYERS:
            raise NotFoundError(
                detail=f"Unknown cartography layer: '{name}'.",
                extra={"supported": sorted(SUPPORTED_LAYERS)},
            )

        cache_key = self._layer_cache_key(name, params, scope_user)
        redis = get_redis()

        # 1. Tentative cache.
        cached: str | None = None
        with contextlib.suppress(Exception):  # pragma: no cover
            cached = await redis.get(cache_key)
        if cached:
            with contextlib.suppress(Exception):
                payload = json.loads(cached)
                payload.setdefault("meta", {})["cached"] = True
                return payload

        # 2. Calcul via la couche layers.py.
        payload = await self._dispatch_layer(name, params, scope_user)

        # 3. Mise en cache.
        with contextlib.suppress(Exception):  # pragma: no cover
            await redis.set(
                cache_key,
                json.dumps(payload, default=str),
                ex=LAYER_CACHE_TTL_SECONDS,
            )
        payload.setdefault("meta", {})["cached"] = False
        return payload

    async def _dispatch_layer(
        self,
        name: str,
        params: dict[str, Any],
        scope_user: User,
    ) -> dict[str, Any]:
        """Aiguillage pur (sans cache) — testable directement."""
        # Les utilisateurs SCHOOL n'ont pas de vue agrégée. On renvoie une
        # collection vide pour ne pas leaker de données hors-périmètre.
        from app.shared.permissions import SCHOOL_SCOPE_ROLES

        if scope_user.role in SCHOOL_SCOPE_ROLES:
            return {
                "type": "FeatureCollection",
                "features": [],
                "meta": {
                    "count": 0,
                    "layer": name,
                    "reason": "out_of_scope",
                },
            }

        if name == "gpi-critical-regions":
            fc = await cartography_layers.get_gpi_critical_regions(
                self.session,
                school_year_id=params.get("schoolYearId"),
            )
        elif name == "capacity-critical-schools":
            fc = await cartography_layers.get_critical_capacity_schools_geo(
                self.session,
                base_school_year_id=params.get("baseSchoolYearId"),
            )
        elif name == "staffing-critical-schools":
            fc = await cartography_layers.get_critical_staffing_schools_geo(
                self.session,
                school_year_id=params.get("schoolYearId"),
            )
        elif name == "infrastructure-gaps":
            fc = await cartography_layers.get_infrastructure_gaps_geo(self.session)
        elif name == "zone-type":
            fc = await cartography_layers.get_zone_type_layer(self.session)
        elif name == "white-zones-enriched":
            fc = await cartography_layers.get_white_zones_enriched(
                self.session,
                radius_km=float(
                    params.get(
                        "radiusKm",
                        cartography_layers.WHITE_ZONE_DEFAULT_RADIUS_KM,
                    )
                ),
                population_threshold=int(
                    params.get(
                        "populationThreshold",
                        cartography_layers.WHITE_ZONE_DEFAULT_POPULATION_THRESHOLD,
                    )
                ),
            )
        elif name == "investment-priority":
            fc = await cartography_layers.get_investment_priority_geo(
                self.session,
            )
        else:  # pragma: no cover - garde-fou défensif
            raise NotFoundError(detail=f"Unknown cartography layer: '{name}'.")

        # Filtrage scope régional appliqué APRÈS calcul (les couches sont
        # nationales par défaut ; on coupe ici pour préserver le secret
        # territorial sur les rôles non-nationaux).
        return self._apply_layer_scope(fc, scope_user, name)

    @staticmethod
    def _apply_layer_scope(
        fc: dict[str, Any], user: User, layer_name: str
    ) -> dict[str, Any]:
        """Si l'utilisateur est REGIONAL_*, filtre les features sur sa région.

        On accepte plusieurs noms de propriétés (``regionId``,
        ``parentRegionId``) pour rester souple selon la couche.
        """
        from app.shared.permissions import (
            NATIONAL_SCOPE_ROLES,
            PREFECTURE_SCOPE_ROLES,
            REGIONAL_SCOPE_ROLES,
        )

        if user.role in NATIONAL_SCOPE_ROLES:
            return fc

        target_region = user.regionId if user.role in REGIONAL_SCOPE_ROLES else None
        target_prefecture = (
            user.prefectureId if user.role in PREFECTURE_SCOPE_ROLES else None
        )

        if not target_region and not target_prefecture:
            return fc

        kept: list[dict[str, Any]] = []
        for feat in fc.get("features", []):
            props = feat.get("properties", {}) or {}
            keep = True
            if target_region is not None:
                # GPI critique régions utilise "regionId" ; les autres
                # exposent un "regionId" hérité de l'école.
                rid = props.get("regionId")
                if rid is not None and rid != target_region:
                    keep = False
            if keep and target_prefecture is not None:
                pid = props.get("prefectureId")
                if pid is not None and pid != target_prefecture:
                    keep = False
            if keep:
                kept.append(feat)

        meta = dict(fc.get("meta", {}))
        meta["count"] = len(kept)
        meta["scopedTo"] = target_region or target_prefecture
        meta["layer"] = layer_name
        return {
            "type": "FeatureCollection",
            "features": kept,
            "meta": meta,
        }
