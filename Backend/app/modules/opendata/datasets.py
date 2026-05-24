"""Module 12 — Registry des 6 datasets publics + fonctions de génération.

Chaque dataset est décrit par un :class:`DatasetSpec` qui contient :

* ``key`` — identifiant stable utilisé dans l'URL publique.
* ``title`` / ``description`` — métadonnées exposées dans le catalogue.
* ``license`` — par défaut ``CC-BY-4.0``.
* ``refresh_frequency`` — cadence indicative.
* ``schema`` — JSON Schema décrivant un record (utilisé pour la validation
  côté consommateur).
* ``fetch`` — coroutine ``(AsyncSession) -> list[dict]``.

Les fonctions ``fetch_*`` font UNIQUEMENT des agrégats par région ou par
école — aucune donnée nominative. Tout dataset qui contiendrait un
``studentId`` / ``firstName`` / etc. serait refusé par
:func:`app.modules.opendata.anonymization.is_anonymous` (garde-fou de test).

Pourquoi un registry statique ?
-------------------------------
Les datasets sont volontairement codés en dur dans ce module : une URL
publique d'open data DOIT rester citable dans une publication académique
pendant plusieurs années. Ajouter / retirer un dataset = modification de
code review-able, pas un opérateur qui clique dans une UI.

Datasets exposés (MVP)
----------------------
1. ``schools_by_region`` — nombre d'écoles/élèves/enseignants par région.
2. ``attendance_rate_by_region`` — taux moyen de présence par région.
3. ``gender_distribution_by_region`` — répartition F/H des élèves par région.
4. ``dropout_risk_by_region`` — comptage HIGH/MEDIUM/LOW basé sur Module 8.
5. ``schools_density`` — densité d'écoles par sous-préfecture (approximation
   par count, surface estimative — MVP sans PostGIS).
6. ``diplomas_issued_by_year`` — comptage des diplômes par année + type.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.attendance.models import AttendanceRecord
from app.modules.census.models import Student, Teacher
from app.modules.diplomas.models import Diploma
from app.modules.predictions.enums import DropoutRiskLevel
from app.modules.predictions.models import DropoutPrediction
from app.modules.schools.models import School
from app.modules.territory.models import Region, SubPrefecture
from app.shared.enums import AttendanceStatus, Gender

# Type alias
FetchFn = Callable[[AsyncSession], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True)
class DatasetSpec:
    """Métadonnées + fonction de fetch d'un dataset open data."""

    key: str
    title: str
    description: str
    refresh_frequency: str
    schema: dict[str, Any]
    fetch: FetchFn
    license: str = "CC-BY-4.0"


# ===========================================================================
# 1. schools_by_region
# ===========================================================================
SCHOOLS_BY_REGION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "regionName": {"type": "string"},
        "schoolCount": {"type": "integer", "minimum": 0},
        "studentCount": {"type": "integer", "minimum": 0},
        "teacherCount": {"type": "integer", "minimum": 0},
    },
    "required": [
        "regionName", "schoolCount", "studentCount", "teacherCount",
    ],
    "additionalProperties": False,
}


async def fetch_schools_by_region(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Pour chaque région, compte écoles + élèves + enseignants.

    Trois requêtes simples agrégées en Python — préférable à un gros JOIN
    qui multiplierait les lignes (un élève par école = N par région).
    """
    # Schools per region (left join pour inclure les régions sans école).
    schools_rows = (await session.execute(
        select(Region.name, func.count(School.id))
        .outerjoin(School, School.regionId == Region.id)
        .group_by(Region.name)
    )).all()

    students_rows = (await session.execute(
        select(Region.name, func.count(Student.id))
        .outerjoin(School, School.regionId == Region.id)
        .outerjoin(Student, Student.schoolId == School.id)
        .group_by(Region.name)
    )).all()

    teachers_rows = (await session.execute(
        select(Region.name, func.count(Teacher.id))
        .outerjoin(School, School.regionId == Region.id)
        .outerjoin(Teacher, Teacher.schoolId == School.id)
        .group_by(Region.name)
    )).all()

    students_by = {name: int(count) for name, count in students_rows}
    teachers_by = {name: int(count) for name, count in teachers_rows}

    out: list[dict[str, Any]] = []
    for region_name, school_count in schools_rows:
        out.append({
            "regionName": region_name,
            "schoolCount": int(school_count),
            "studentCount": students_by.get(region_name, 0),
            "teacherCount": teachers_by.get(region_name, 0),
        })
    out.sort(key=lambda r: r["regionName"])
    return out


# ===========================================================================
# 2. attendance_rate_by_region
# ===========================================================================
ATTENDANCE_RATE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "regionName": {"type": "string"},
        "attendanceRate": {
            "type": "number", "minimum": 0.0, "maximum": 1.0,
        },
        "observationCount": {"type": "integer", "minimum": 0},
    },
    "required": ["regionName", "attendanceRate", "observationCount"],
    "additionalProperties": False,
}


async def fetch_attendance_rate_by_region(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Taux de présence moyen par région (sur toutes les observations).

    Le taux est calculé comme ``PRESENT / total``. ``LATE`` n'est PAS
    comptabilisée comme présence (les enseignants peuvent ajuster ça
    plus tard si besoin). Une région sans observation renvoie
    ``attendanceRate=0.0`` et ``observationCount=0``.
    """
    stmt = (
        select(
            Region.name,
            func.count(AttendanceRecord.id).label("total"),
            func.sum(
                case(
                    (
                        AttendanceRecord.status == AttendanceStatus.PRESENT,
                        1,
                    ),
                    else_=0,
                )
            ).label("present"),
        )
        .outerjoin(School, School.regionId == Region.id)
        .outerjoin(
            AttendanceRecord, AttendanceRecord.schoolId == School.id,
        )
        .group_by(Region.name)
    )
    rows = (await session.execute(stmt)).all()

    out: list[dict[str, Any]] = []
    for region_name, total, present in rows:
        total_int = int(total or 0)
        present_int = int(present or 0)
        rate = round(present_int / total_int, 4) if total_int else 0.0
        out.append({
            "regionName": region_name,
            "attendanceRate": rate,
            "observationCount": total_int,
        })
    out.sort(key=lambda r: r["regionName"])
    return out


# ===========================================================================
# 3. gender_distribution_by_region
# ===========================================================================
GENDER_DISTRIBUTION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "regionName": {"type": "string"},
        "maleCount": {"type": "integer", "minimum": 0},
        "femaleCount": {"type": "integer", "minimum": 0},
        "ratio": {"type": "number", "minimum": 0.0},
    },
    "required": ["regionName", "maleCount", "femaleCount", "ratio"],
    "additionalProperties": False,
}


async def fetch_gender_distribution_by_region(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Comptage F/H par région + ratio F/H (gender parity index).

    Le ratio est défini comme ``female / male`` (convention UNESCO GPI).
    Si ``male == 0`` on renvoie ``ratio = 0.0`` pour éviter une division
    par zéro plutôt qu'un infini qui casse les visualisations.
    """
    stmt = (
        select(
            Region.name,
            func.sum(
                case((Student.gender == Gender.MALE, 1), else_=0),
            ).label("male"),
            func.sum(
                case((Student.gender == Gender.FEMALE, 1), else_=0),
            ).label("female"),
        )
        .outerjoin(School, School.regionId == Region.id)
        .outerjoin(Student, Student.schoolId == School.id)
        .group_by(Region.name)
    )
    rows = (await session.execute(stmt)).all()

    out: list[dict[str, Any]] = []
    for region_name, male, female in rows:
        male_int = int(male or 0)
        female_int = int(female or 0)
        ratio = round(female_int / male_int, 4) if male_int else 0.0
        out.append({
            "regionName": region_name,
            "maleCount": male_int,
            "femaleCount": female_int,
            "ratio": ratio,
        })
    out.sort(key=lambda r: r["regionName"])
    return out


# ===========================================================================
# 4. dropout_risk_by_region (basé Module 8)
# ===========================================================================
DROPOUT_RISK_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "regionName": {"type": "string"},
        "highRiskCount": {"type": "integer", "minimum": 0},
        "mediumRiskCount": {"type": "integer", "minimum": 0},
        "lowRiskCount": {"type": "integer", "minimum": 0},
    },
    "required": [
        "regionName", "highRiskCount", "mediumRiskCount", "lowRiskCount",
    ],
    "additionalProperties": False,
}


async def fetch_dropout_risk_by_region(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Comptage HIGH/MEDIUM/LOW des prédictions de décrochage par région.

    Source : :class:`DropoutPrediction` (Module 8). Si Module 8 n'a pas
    encore produit de prédictions, on renvoie 0 partout — pas d'erreur.
    """
    stmt = (
        select(
            Region.name,
            func.sum(
                case(
                    (
                        DropoutPrediction.riskLevel == DropoutRiskLevel.HIGH,
                        1,
                    ),
                    else_=0,
                )
            ).label("high"),
            func.sum(
                case(
                    (
                        DropoutPrediction.riskLevel == DropoutRiskLevel.MEDIUM,
                        1,
                    ),
                    else_=0,
                )
            ).label("medium"),
            func.sum(
                case(
                    (
                        DropoutPrediction.riskLevel == DropoutRiskLevel.LOW,
                        1,
                    ),
                    else_=0,
                )
            ).label("low"),
        )
        .outerjoin(School, School.regionId == Region.id)
        .outerjoin(Student, Student.schoolId == School.id)
        .outerjoin(
            DropoutPrediction, DropoutPrediction.studentId == Student.id,
        )
        .group_by(Region.name)
    )
    rows = (await session.execute(stmt)).all()

    out: list[dict[str, Any]] = []
    for region_name, high, medium, low in rows:
        out.append({
            "regionName": region_name,
            "highRiskCount": int(high or 0),
            "mediumRiskCount": int(medium or 0),
            "lowRiskCount": int(low or 0),
        })
    out.sort(key=lambda r: r["regionName"])
    return out


# ===========================================================================
# 5. schools_density (par sous-préfecture)
# ===========================================================================
SCHOOLS_DENSITY_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "subPrefectureName": {"type": "string"},
        "schoolDensity": {"type": "number", "minimum": 0.0},
        "areaKm2": {"type": "number", "minimum": 0.0},
    },
    "required": ["subPrefectureName", "schoolDensity", "areaKm2"],
    "additionalProperties": False,
}

# MVP : on n'a pas PostGIS partout, on prend une surface forfaitaire
# par sous-préfecture (≈ 600 km² — moyenne nationale). Le jour où
# Module 5 (cartographie) calcule la vraie surface depuis le polygone,
# on remplacera cette constante par un JOIN sur la colonne géographique.
_DEFAULT_SUBPREFECTURE_AREA_KM2 = 600.0


async def fetch_schools_density(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Densité d'écoles par sous-préfecture (écoles / km²).

    MVP sans PostGIS : on utilise une surface forfaitaire. Le calcul
    reste représentatif pour comparer les sous-préfectures entre elles
    (toutes utilisent la même base). À remplacer par
    ``ST_Area(SubPrefecture.geom)`` quand Module 5 sera disponible.
    """
    stmt = (
        select(
            SubPrefecture.name, func.count(School.id),
        )
        .outerjoin(School, School.subPrefectureId == SubPrefecture.id)
        .group_by(SubPrefecture.name)
    )
    rows = (await session.execute(stmt)).all()

    out: list[dict[str, Any]] = []
    for name, count in rows:
        density = round(int(count) / _DEFAULT_SUBPREFECTURE_AREA_KM2, 4)
        out.append({
            "subPrefectureName": name,
            "schoolDensity": density,
            "areaKm2": _DEFAULT_SUBPREFECTURE_AREA_KM2,
        })
    out.sort(key=lambda r: r["subPrefectureName"])
    return out


# ===========================================================================
# 6. diplomas_issued_by_year
# ===========================================================================
DIPLOMAS_ISSUED_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "year": {"type": "integer", "minimum": 1900},
        "diplomaType": {"type": "string"},
        "count": {"type": "integer", "minimum": 0},
    },
    "required": ["year", "diplomaType", "count"],
    "additionalProperties": False,
}


async def fetch_diplomas_issued_by_year(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Comptage des diplômes signés (statut ISSUED) par année + type.

    L'année est extraite du serial (format ``{TYPE}-{YEAR}-{HEX}``) plutôt
    que de ``issuedAt`` pour rester aligné sur la convention d'émission.
    Un diplôme REVOKED n'est PAS compté (un diplôme révoqué = "n'existait
    pas vraiment" pour les statistiques publiques).
    """
    # On fetch (diplomaType, serial) puis on agrège en Python : la
    # cardinalité reste petite (au plus quelques milliers / an).
    from app.modules.diplomas.enums import DiplomaStatus

    rows = (await session.execute(
        select(Diploma.diplomaType, Diploma.serial)
        .where(Diploma.status == DiplomaStatus.ISSUED)
    )).all()

    buckets: dict[tuple[int, str], int] = {}
    for diploma_type, serial in rows:
        # Format ``{TYPE}-{YEAR}-{HEX}`` (cf. diplomas.serial.generate_serial).
        parts = serial.split("-")
        if len(parts) < 3 or not parts[1].isdigit():
            continue
        year = int(parts[1])
        type_value = (
            diploma_type.value
            if hasattr(diploma_type, "value")
            else str(diploma_type)
        )
        key = (year, type_value)
        buckets[key] = buckets.get(key, 0) + 1

    out: list[dict[str, Any]] = []
    for (year, type_value), count in buckets.items():
        out.append({
            "year": year,
            "diplomaType": type_value,
            "count": count,
        })
    out.sort(key=lambda r: (r["year"], r["diplomaType"]))
    return out


# ===========================================================================
# Registry — ordre = ordre d'apparition dans le catalogue public
# ===========================================================================
DATASETS: list[DatasetSpec] = [
    DatasetSpec(
        key="schools_by_region",
        title="Établissements par région",
        description=(
            "Pour chaque région administrative : nombre d'écoles, "
            "d'élèves et d'enseignants enregistrés. Aucun PII."
        ),
        refresh_frequency="daily",
        schema=SCHOOLS_BY_REGION_SCHEMA,
        fetch=fetch_schools_by_region,
    ),
    DatasetSpec(
        key="attendance_rate_by_region",
        title="Taux de présence moyen par région",
        description=(
            "Ratio observations PRESENT / total des observations "
            "d'attendance par région. Calculé sur tout l'historique."
        ),
        refresh_frequency="weekly",
        schema=ATTENDANCE_RATE_SCHEMA,
        fetch=fetch_attendance_rate_by_region,
    ),
    DatasetSpec(
        key="gender_distribution_by_region",
        title="Répartition F/H des élèves par région",
        description=(
            "Comptage masculin/féminin des élèves et indice de parité "
            "F/H (GPI). 0.0 si aucun élève masculin enregistré."
        ),
        refresh_frequency="monthly",
        schema=GENDER_DISTRIBUTION_SCHEMA,
        fetch=fetch_gender_distribution_by_region,
    ),
    DatasetSpec(
        key="dropout_risk_by_region",
        title="Risque de décrochage scolaire par région",
        description=(
            "Comptage des élèves classés HIGH/MEDIUM/LOW par le modèle "
            "de prédiction (Module 8). Source : DropoutPrediction."
        ),
        refresh_frequency="weekly",
        schema=DROPOUT_RISK_SCHEMA,
        fetch=fetch_dropout_risk_by_region,
    ),
    DatasetSpec(
        key="schools_density",
        title="Densité d'écoles par sous-préfecture",
        description=(
            "Nombre d'écoles divisé par la surface (km²) de la "
            "sous-préfecture. Surface forfaitaire en MVP (600 km²) en "
            "attendant l'intégration PostGIS (Module 5)."
        ),
        refresh_frequency="monthly",
        schema=SCHOOLS_DENSITY_SCHEMA,
        fetch=fetch_schools_density,
    ),
    DatasetSpec(
        key="diplomas_issued_by_year",
        title="Diplômes nationaux délivrés par année",
        description=(
            "Pour chaque année et chaque type de diplôme (CEPE, BEPC, "
            "CFEE), nombre de diplômes signés (statut ISSUED). Les "
            "diplômes REVOKED ne sont PAS comptés."
        ),
        refresh_frequency="yearly",
        schema=DIPLOMAS_ISSUED_SCHEMA,
        fetch=fetch_diplomas_issued_by_year,
    ),
]


def get_dataset_spec(key: str) -> DatasetSpec | None:
    """Lookup d'un dataset par sa key, ``None`` si inconnu."""
    for spec in DATASETS:
        if spec.key == key:
            return spec
    return None


__all__ = [
    "DATASETS",
    "DatasetSpec",
    "fetch_attendance_rate_by_region",
    "fetch_diplomas_issued_by_year",
    "fetch_dropout_risk_by_region",
    "fetch_gender_distribution_by_region",
    "fetch_schools_by_region",
    "fetch_schools_density",
    "get_dataset_spec",
]
