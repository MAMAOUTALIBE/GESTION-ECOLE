"""Module 10 — Assistant tools (function calling Claude).

Chaque tool est :
1. Un schéma JSON Schema exposé au LLM (clé ``TOOLS``).
2. Une fonction async côté backend (clé ``TOOL_DISPATCH``).

**Règle d'or RBAC** : ``execute_tool`` prend toujours le ``current_user``
en paramètre et applique son scope territorial à TOUTES les requêtes :

* ``NATIONAL_ADMIN`` / ``MINISTRY_ADMIN`` → pas de filtre.
* ``REGIONAL_ADMIN`` / ``INSPECTOR`` → filtre WHERE ``regionId`` =
  ``user.regionId``.
* ``PREFECTURE_ADMIN`` / ``SUB_PREFECTURE_ADMIN`` → idem region (les
  prefecture/sub IDs ne sont pas dénormalisés sur Student/School
  systématiquement, on retombe sur la région).
* ``SCHOOL_DIRECTOR`` / ``TEACHER`` / ``CENSUS_AGENT`` → filtre WHERE
  ``schoolId`` = ``user.schoolId`` (donc IMPOSSIBLE pour un directeur de
  compter les élèves d'une autre école même s'il demande explicitement).

Si un argument ``schoolId`` est passé par le LLM mais qu'il sort du scope
de l'utilisateur, on l'écrase silencieusement par le scope du user (plutôt
que de renvoyer une erreur exploitable par injection).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.attendance.models import AttendanceRecord
from app.modules.census.models import Student, Teacher
from app.modules.predictions.enums import DropoutRiskLevel
from app.modules.predictions.models import DropoutPrediction
from app.modules.schools.models import School
from app.shared.enums import AttendanceStatus, Gender, UserRole

if TYPE_CHECKING:
    from app.modules.auth.models import User


# ---------------------------------------------------------------------------
# Tool schemas (envoyés à Claude via `tools=` dans messages.create)
# ---------------------------------------------------------------------------
TOOLS: list[dict[str, Any]] = [
    {
        "name": "count_students",
        "description": (
            "Compte le nombre d'élèves répondant à des filtres optionnels. "
            "RBAC : restreint automatiquement au périmètre de l'utilisateur."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "schoolId": {
                    "type": "string",
                    "description": "ID école (optionnel) pour restreindre.",
                },
                "regionId": {
                    "type": "string",
                    "description": "ID région (optionnel).",
                },
                "gender": {
                    "type": "string",
                    "enum": ["FEMALE", "MALE", "OTHER"],
                    "description": "Restreint à un genre.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "count_schools",
        "description": (
            "Compte le nombre d'écoles répondant à des filtres optionnels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "regionId": {"type": "string"},
                "prefectureId": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "list_schools_without_teacher",
        "description": (
            "Liste les écoles qui n'ont aucun enseignant rattaché. "
            "Retourne max 50 résultats."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "get_attendance_rate",
        "description": (
            "Calcule le taux de présence (présents / total) sur une période. "
            "schoolId obligatoire (ou implicite depuis le scope user)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "schoolId": {"type": "string"},
                "dateFrom": {
                    "type": "string",
                    "format": "date",
                    "description": "ISO date (YYYY-MM-DD).",
                },
                "dateTo": {"type": "string", "format": "date"},
            },
            "required": [],
        },
    },
    {
        "name": "get_at_risk_students",
        "description": (
            "Liste les élèves à risque d'abandon (dernière prédiction). "
            "Niveau par défaut : HIGH."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "schoolId": {"type": "string"},
                "level": {
                    "type": "string",
                    "enum": ["LOW", "MEDIUM", "HIGH"],
                    "default": "HIGH",
                },
            },
            "required": [],
        },
    },
]

TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in TOOLS)


# ---------------------------------------------------------------------------
# RBAC helpers
# ---------------------------------------------------------------------------
def _user_scope(user: "User") -> dict[str, str | None]:
    """Renvoie ``{"schoolId": ..., "regionId": ...}`` selon le rôle.

    Une seule des deux clés est non-nulle (sauf cas national admin où les
    deux sont None = pas de filtre).
    """
    role = user.role
    if role in (UserRole.NATIONAL_ADMIN, UserRole.MINISTRY_ADMIN):
        return {"schoolId": None, "regionId": None}
    if role in (UserRole.SCHOOL_DIRECTOR, UserRole.TEACHER, UserRole.CENSUS_AGENT):
        # Strict scope école — même si le user a un regionId, on bloque tout
        # ce qui sort de son école.
        return {"schoolId": user.schoolId, "regionId": None}
    # Regional / inspector / prefecture / sub-prefecture : on retombe sur
    # la région (les colonnes prefectureId ne sont pas dénormalisées
    # partout).
    return {"schoolId": None, "regionId": user.regionId}


def _apply_school_scope(stmt: Any, user_scope: dict[str, str | None],
                        requested_school: str | None,
                        school_col: Any) -> Any:
    """Applique un filtre schoolId en arbitrant :

    1. Si le user a un ``schoolId`` (directeur d'école), on l'IMPOSE
       quel que soit le ``requested_school`` demandé par le LLM —
       évite l'escalade de privilèges via injection de prompt.
    2. Sinon si le LLM demande un ``schoolId`` précis, on le respecte.
    3. Sinon pas de filtre school.
    """
    if user_scope["schoolId"]:
        return stmt.where(school_col == user_scope["schoolId"])
    if requested_school:
        return stmt.where(school_col == requested_school)
    return stmt


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
async def _tool_count_students(
    session: AsyncSession,
    user: "User",
    *,
    schoolId: str | None = None,
    regionId: str | None = None,
    gender: str | None = None,
) -> dict[str, Any]:
    scope = _user_scope(user)
    stmt = select(func.count(Student.id))

    stmt = _apply_school_scope(stmt, scope, schoolId, Student.schoolId)

    # Region scope — Student n'a pas de regionId direct → on passe par School.
    if scope["regionId"] or (regionId and not scope["schoolId"]):
        # Le LLM peut demander une autre region : on n'écrase pas, on
        # restreint à l'intersection (region demandée ∩ region du user).
        effective_region = scope["regionId"] or regionId
        # Sub-select School.id WHERE regionId = effective_region
        sub = select(School.id).where(School.regionId == effective_region).subquery()
        stmt = stmt.where(Student.schoolId.in_(select(sub.c.id)))

    if gender:
        try:
            gender_enum = Gender(gender)
        except ValueError:
            return {"error": f"gender invalide: {gender}"}
        stmt = stmt.where(Student.gender == gender_enum)

    total = (await session.execute(stmt)).scalar_one() or 0
    return {
        "count": int(total),
        "filters": {
            "schoolId": scope["schoolId"] or schoolId,
            "regionId": scope["regionId"] or regionId,
            "gender": gender,
        },
    }


async def _tool_count_schools(
    session: AsyncSession,
    user: "User",
    *,
    regionId: str | None = None,
    prefectureId: str | None = None,
) -> dict[str, Any]:
    scope = _user_scope(user)
    stmt = select(func.count(School.id))

    # Si l'utilisateur est rattaché à une école, il ne peut compter que la
    # sienne (count = 1 ou 0).
    if scope["schoolId"]:
        stmt = stmt.where(School.id == scope["schoolId"])
    else:
        effective_region = scope["regionId"] or regionId
        if effective_region:
            stmt = stmt.where(School.regionId == effective_region)
        if prefectureId:
            stmt = stmt.where(School.prefectureId == prefectureId)

    total = (await session.execute(stmt)).scalar_one() or 0
    return {
        "count": int(total),
        "filters": {
            "regionId": scope["regionId"] or regionId,
            "prefectureId": prefectureId,
        },
    }


async def _tool_list_schools_without_teacher(
    session: AsyncSession,
    user: "User",
    *,
    limit: int = 50,
) -> dict[str, Any]:
    scope = _user_scope(user)
    # Sub-select: schoolIds qui ont >= 1 Teacher.
    teacher_school = select(Teacher.schoolId.distinct()).subquery()

    stmt = select(School.id, School.name, School.code).where(
        School.id.notin_(select(teacher_school.c.schoolId)),
    )
    if scope["schoolId"]:
        stmt = stmt.where(School.id == scope["schoolId"])
    elif scope["regionId"]:
        stmt = stmt.where(School.regionId == scope["regionId"])

    stmt = stmt.limit(max(1, min(limit, 200)))
    rows = (await session.execute(stmt)).all()
    return {
        "schools": [
            {"id": r.id, "name": r.name, "code": r.code} for r in rows
        ],
        "count": len(rows),
    }


async def _tool_get_attendance_rate(
    session: AsyncSession,
    user: "User",
    *,
    schoolId: str | None = None,
    dateFrom: str | None = None,
    dateTo: str | None = None,
) -> dict[str, Any]:
    scope = _user_scope(user)
    # Parse dates avec un défaut sain (30 derniers jours).
    try:
        date_to = (
            datetime.fromisoformat(dateTo).replace(tzinfo=UTC) if dateTo
            else datetime.now(UTC)
        )
        date_from = (
            datetime.fromisoformat(dateFrom).replace(tzinfo=UTC) if dateFrom
            else date_to - timedelta(days=30)
        )
    except ValueError as exc:
        return {"error": f"date invalide: {exc}"}

    if date_from > date_to:
        return {"error": "dateFrom doit précéder dateTo"}

    from sqlalchemy import Integer

    stmt = select(
        func.count(AttendanceRecord.id),
        func.sum(
            func.cast(
                AttendanceRecord.status == AttendanceStatus.PRESENT,
                type_=Integer,
            ),
        ),
    ).where(
        and_(
            AttendanceRecord.scannedAt >= date_from,
            AttendanceRecord.scannedAt <= date_to,
        ),
    )
    stmt = _apply_school_scope(stmt, scope, schoolId, AttendanceRecord.schoolId)
    # Region scope (regional admin sans school) : on filtre par schools
    if scope["regionId"] and not scope["schoolId"]:
        sub = select(School.id).where(School.regionId == scope["regionId"]).subquery()
        stmt = stmt.where(AttendanceRecord.schoolId.in_(select(sub.c.id)))

    row = (await session.execute(stmt)).one()
    total = int(row[0] or 0)
    present = int(row[1] or 0)
    rate = (present / total) if total else 0.0
    return {
        "rate": round(rate, 4),
        "present": present,
        "total": total,
        "dateFrom": date_from.date().isoformat(),
        "dateTo": date_to.date().isoformat(),
        "schoolId": scope["schoolId"] or schoolId,
    }


async def _tool_get_at_risk_students(
    session: AsyncSession,
    user: "User",
    *,
    schoolId: str | None = None,
    level: str = "HIGH",
) -> dict[str, Any]:
    scope = _user_scope(user)
    try:
        risk = DropoutRiskLevel(level)
    except ValueError:
        return {"error": f"level invalide: {level}"}

    # Dernière prédiction par élève (sous-requête).
    last_pred = (
        select(
            DropoutPrediction.studentId,
            func.max(DropoutPrediction.computedAt).label("maxAt"),
        )
        .group_by(DropoutPrediction.studentId)
        .subquery()
    )

    stmt = (
        select(
            Student.id,
            Student.firstName,
            Student.lastName,
            Student.uniqueCode,
            DropoutPrediction.probability,
        )
        .join(DropoutPrediction, DropoutPrediction.studentId == Student.id)
        .join(
            last_pred,
            and_(
                last_pred.c.studentId == DropoutPrediction.studentId,
                last_pred.c.maxAt == DropoutPrediction.computedAt,
            ),
        )
        .where(DropoutPrediction.riskLevel == risk)
    )
    stmt = _apply_school_scope(stmt, scope, schoolId, Student.schoolId)
    if scope["regionId"] and not scope["schoolId"]:
        sub = select(School.id).where(School.regionId == scope["regionId"]).subquery()
        stmt = stmt.where(Student.schoolId.in_(select(sub.c.id)))

    stmt = stmt.limit(50)
    rows = (await session.execute(stmt)).all()
    return {
        "students": [
            {
                "id": r.id,
                "firstName": r.firstName,
                "lastName": r.lastName,
                "uniqueCode": r.uniqueCode,
                "probability": round(float(r.probability), 4),
            }
            for r in rows
        ],
        "count": len(rows),
        "level": level,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
TOOL_DISPATCH = {
    "count_students": _tool_count_students,
    "count_schools": _tool_count_schools,
    "list_schools_without_teacher": _tool_list_schools_without_teacher,
    "get_attendance_rate": _tool_get_attendance_rate,
    "get_at_risk_students": _tool_get_at_risk_students,
}


async def execute_tool(
    name: str,
    args: dict[str, Any] | None,
    current_user: "User",
    session: AsyncSession,
) -> dict[str, Any]:
    """Dispatcher unique appelé à la fois par le mode LLM et le mode scripté.

    Renvoie toujours un dict (jamais une exception) — c'est ce dict qui est
    sérialisé en JSON puis renvoyé soit au LLM (via tool_result), soit
    affiché à l'utilisateur via le scripted fallback.
    """
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return {"error": f"tool inconnu: {name}"}
    safe_args = dict(args or {})
    try:
        return await fn(session, current_user, **safe_args)
    except TypeError as exc:
        # Arg invalide passé par le LLM (ex : kwarg inconnu).
        return {"error": f"arguments invalides pour {name}: {exc}"}


__all__ = ["TOOLS", "TOOL_DISPATCH", "TOOL_NAMES", "execute_tool"]
