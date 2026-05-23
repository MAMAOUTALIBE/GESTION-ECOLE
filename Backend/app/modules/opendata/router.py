"""Phase 14 — Portail Open Data (statistiques publiques anonymisées).

Endpoints **PUBLICS** sans authentification — données agrégées uniquement,
aucun accès aux noms d'élèves/enseignants. Conforme RGPD-like.

Rate limit naturel par Postgres + capping des limites query.
"""
from fastapi import APIRouter, Query
from sqlalchemy import func, select
from typing import Annotated

from app.modules.census.models import Student, Teacher
from app.modules.schools.models import ClassRoom, School
from app.modules.territory.models import Region
from app.shared.deps import DbSession
from app.shared.enums import (
    ElectricitySource,
    SchoolAffiliation,
    WaterSource,
)

router = APIRouter(tags=["opendata"])


@router.get(
    "/national-stats",
    summary="Indicateurs nationaux publics — sans authentification",
)
async def national_stats(session: DbSession) -> dict:
    """KPIs nationaux publiquement disponibles (transparence ministérielle)."""
    students = (await session.execute(
        select(func.count()).select_from(Student)
    )).scalar_one()
    teachers = (await session.execute(
        select(func.count()).select_from(Teacher)
    )).scalar_one()
    schools = (await session.execute(
        select(func.count()).select_from(School)
    )).scalar_one()
    classes = (await session.execute(
        select(func.count()).select_from(ClassRoom)
    )).scalar_one()
    regions = (await session.execute(
        select(func.count()).select_from(Region)
    )).scalar_one()

    # Couvertures infrastructure (Phase 10)
    schools_with_water = (await session.execute(
        select(func.count()).select_from(School).where(
            School.waterSource.isnot(None),
            School.waterSource != WaterSource.NONE,
        )
    )).scalar_one()
    schools_with_elec = (await session.execute(
        select(func.count()).select_from(School).where(
            School.electricitySource.isnot(None),
            School.electricitySource != ElectricitySource.NONE,
        )
    )).scalar_one()

    return {
        "country": "Guinée",
        "totals": {
            "students": students,
            "teachers": teachers,
            "schools": schools,
            "classes": classes,
            "regions": regions,
        },
        "ratios": {
            "studentsPerTeacher": round(students / teachers, 1) if teachers else 0,
            "studentsPerSchool": round(students / schools, 1) if schools else 0,
            "averageClassSize": round(students / classes, 1) if classes else 0,
        },
        "coverage": {
            "waterAccessPct": round((schools_with_water / schools) * 100) if schools else 0,
            "electricityAccessPct": round((schools_with_elec / schools) * 100) if schools else 0,
        },
        "license": "CC BY 4.0",
        "source": "Ministère de l'Éducation Nationale, Guinée",
    }


@router.get(
    "/by-region",
    summary="Statistiques publiques par région",
)
async def by_region(session: DbSession) -> list[dict]:
    """Tableau public : 1 ligne par région avec effectifs et couvertures."""
    stmt = (
        select(
            Region.id, Region.name,
            func.count(School.id.distinct()).label("schools"),
        )
        .outerjoin(School, School.regionId == Region.id)
        .group_by(Region.id, Region.name)
        .order_by(Region.name.asc())
    )
    region_rows = (await session.execute(stmt)).all()

    out = []
    for r in region_rows:
        students = (await session.execute(
            select(func.count()).select_from(Student)
            .join(School, School.id == Student.schoolId)
            .where(School.regionId == r.id)
        )).scalar_one()
        teachers = (await session.execute(
            select(func.count()).select_from(Teacher)
            .join(School, School.id == Teacher.schoolId)
            .where(School.regionId == r.id)
        )).scalar_one()
        out.append({
            "regionId": r.id,
            "regionName": r.name,
            "schools": int(r.schools),
            "students": students,
            "teachers": teachers,
            "studentsPerTeacher": round(students / teachers, 1) if teachers else 0,
        })
    return out


@router.get(
    "/affiliations",
    summary="Répartition des écoles par affiliation (PUBLIC, PRIVÉ, CATHOLIQUE…)",
)
async def affiliations(session: DbSession) -> dict:
    rows = (await session.execute(
        select(School.affiliation, func.count())
        .group_by(School.affiliation)
    )).all()
    return {
        "breakdown": [
            {"affiliation": a.value if a else "NON_RENSEIGNE", "count": int(n)}
            for a, n in rows
        ],
    }
