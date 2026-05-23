"""Cartography service — PostGIS-backed spatial queries for the school map.

All public methods accept the authenticated `User` and apply the same
territorial scope filtering as the schools/census modules.
"""
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.modules.auth.models import User
from app.modules.cartography.schemas import (
    CatchmentsQuery,
    CoverageGapsQuery,
    Feature,
    FeatureCollection,
    IndicatorsResponse,
    NearbyQuery,
    NearbyResponse,
    NearbySchool,
    SchoolsGeoQuery,
    TerritoryIndicator,
)
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
