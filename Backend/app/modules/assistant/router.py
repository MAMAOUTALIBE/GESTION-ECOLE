"""Phase 14 — Assistant LLM (Claude API) connecté à la base de données.

Pattern : function calling. Le LLM peut appeler 5 outils (tools) qui
interrogent les services métier en lecture seule. Les noms d'élèves
et d'enseignants sont systématiquement retirés des réponses du tool —
seules des données agrégées remontent au LLM.

Si `ANTHROPIC_API_KEY` n'est pas configurée, l'endpoint retourne 503 avec
un message clair, et un mode "explications scriptées" reste disponible.
"""
import json
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.modules.auth.models import User
from app.modules.census.models import Student, Teacher
from app.modules.schools.models import School
from app.modules.territory.models import Region
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

router = APIRouter(tags=["assistant"])

ASSISTANT_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.INSPECTOR,
)


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=2, max_length=4000)
    conversationId: str | None = None


class ChatResponse(BaseModel):
    reply: str
    citations: list[dict] = []
    toolsUsed: list[str] = []


# =====================================================================
# Tools — fonctions exposées au LLM (lecture seule, données agrégées)
# =====================================================================
TOOLS = [
    {
        "name": "get_national_kpis",
        "description": "Indicateurs nationaux : effectifs, ratios, couverture GPS.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_regions_breakdown",
        "description": "Décomposition par région (4 régions de Guinée).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "find_at_risk_schools",
        "description": (
            "Liste les écoles avec ratio élèves/enseignant supérieur au seuil "
            "fourni (par défaut 45)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "threshold": {"type": "integer", "default": 45},
                "limit": {"type": "integer", "default": 10},
            },
            "required": [],
        },
    },
    {
        "name": "count_dropout_critical",
        "description": "Nombre d'élèves en risque critique de décrochage.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


async def _tool_get_national_kpis(session, user) -> dict:
    students = (await session.execute(select(func.count()).select_from(Student))).scalar_one()
    teachers = (await session.execute(select(func.count()).select_from(Teacher))).scalar_one()
    schools = (await session.execute(select(func.count()).select_from(School))).scalar_one()
    return {
        "students": students, "teachers": teachers, "schools": schools,
        "studentsPerTeacher": round(students / teachers, 1) if teachers else 0,
    }


async def _tool_get_regions_breakdown(session, user) -> list[dict]:
    rows = (await session.execute(
        select(
            Region.name,
            func.count(School.id.distinct()).label("n_schools"),
        )
        .outerjoin(School, School.regionId == Region.id)
        .group_by(Region.id, Region.name)
    )).all()
    return [{"region": r.name, "schools": int(r.n_schools)} for r in rows]


async def _tool_find_at_risk_schools(session, user, threshold=45, limit=10) -> list[dict]:
    teacher_subq = (
        select(Teacher.schoolId, func.count().label("n_t"))
        .group_by(Teacher.schoolId).subquery()
    )
    student_subq = (
        select(Student.schoolId, func.count().label("n_s"))
        .group_by(Student.schoolId).subquery()
    )
    stmt = (
        select(
            School.name,
            School.code,
            student_subq.c.n_s,
            teacher_subq.c.n_t,
        )
        .outerjoin(teacher_subq, teacher_subq.c.schoolId == School.id)
        .outerjoin(student_subq, student_subq.c.schoolId == School.id)
        .limit(500)
    )
    rows = (await session.execute(stmt)).all()
    candidates = []
    for r in rows:
        ns = int(r.n_s or 0)
        nt = int(r.n_t or 0)
        if nt == 0 and ns > 0:
            candidates.append({"school": r.name, "code": r.code, "ratio": "infinity",
                               "students": ns, "teachers": 0})
        elif nt > 0 and ns / nt > threshold:
            candidates.append({"school": r.name, "code": r.code,
                               "ratio": round(ns / nt, 1),
                               "students": ns, "teachers": nt})
    candidates.sort(
        key=lambda x: (-1 if x["ratio"] == "infinity" else -x["ratio"]),
    )
    return candidates[:limit]


async def _tool_count_dropout_critical(session, user) -> dict:
    # Approximation rapide : élèves avec absence_rate >= 50%
    from app.modules.attendance.models import AttendanceRecord
    from app.shared.enums import AttendanceStatus
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(days=30)
    result = (await session.execute(
        select(
            AttendanceRecord.studentId,
            func.count().label("total"),
            func.sum(
                func.cast(AttendanceRecord.status == AttendanceStatus.ABSENT,
                          type_=__import__("sqlalchemy").Integer)
            ).label("absent"),
        )
        .where(AttendanceRecord.scannedAt >= cutoff,
               AttendanceRecord.studentId.isnot(None))
        .group_by(AttendanceRecord.studentId)
    )).all()
    critical_count = sum(
        1 for r in result
        if int(r.total) > 0 and int(r.absent or 0) / int(r.total) >= 0.5
    )
    return {"criticalCount": critical_count, "totalAnalyzed": len(result)}


TOOL_DISPATCH = {
    "get_national_kpis": _tool_get_national_kpis,
    "get_regions_breakdown": _tool_get_regions_breakdown,
    "find_at_risk_schools": _tool_find_at_risk_schools,
    "count_dropout_critical": _tool_count_dropout_critical,
}


SYSTEM_PROMPT = """Tu es l'assistant analytique du ministère de l'Éducation \
nationale de Guinée. Tu réponds en français avec rigueur et concision.

Tu as accès à 4 outils en lecture seule sur la base de données. \
Utilise-les systématiquement avant de répondre. \
N'invente JAMAIS de chiffres — si tu ne peux pas vérifier, dis-le.

Style : réponse de 2-5 phrases, chiffres clés mis en gras avec **gras**, \
toujours citer la source (« Source : tool get_national_kpis »).
"""


@router.post(
    "/chat",
    response_model=ChatResponse,
    dependencies=[Depends(require_roles(*ASSISTANT_ROLES))],
    summary="Pose une question en français à l'assistant — il interroge la base",
)
async def chat(
    dto: ChatRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: DbSession,
) -> ChatResponse:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Mode démo sans clé : on exécute directement les tools selon mots-clés
        return await _scripted_response(dto.message, session, user)

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise HTTPException(503, "SDK Anthropic non installé.")

    client = AsyncAnthropic(api_key=api_key)
    messages = [{"role": "user", "content": dto.message}]
    tools_used: list[str] = []

    # Boucle d'appel multi-tour avec tool use
    for _ in range(5):  # max 5 tours
        response = await client.messages.create(
            model="claude-sonnet-4-6",  # modèle disponible le plus capable
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        if response.stop_reason == "tool_use":
            # Extrait les tool calls et exécute
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tools_used.append(block.name)
                    fn = TOOL_DISPATCH.get(block.name)
                    if fn is None:
                        result = {"error": f"Tool inconnu : {block.name}"}
                    else:
                        result = await fn(session, user, **(block.input or {}))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str, ensure_ascii=False),
                    })
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            continue
        # Réponse finale
        text_blocks = [b.text for b in response.content if b.type == "text"]
        return ChatResponse(
            reply="\n".join(text_blocks),
            toolsUsed=tools_used,
        )

    return ChatResponse(
        reply="(Trop d'aller-retours avec les outils, abandonné)",
        toolsUsed=tools_used,
    )


async def _scripted_response(message: str, session, user) -> ChatResponse:
    """Mode dégradé sans clé API : réponses scriptées basées sur mots-clés."""
    msg = message.lower()
    tools_used = []
    parts = []

    if any(kw in msg for kw in ["combien", "kpi", "indicateur", "national"]):
        kpis = await _tool_get_national_kpis(session, user)
        tools_used.append("get_national_kpis")
        parts.append(
            f"📊 Indicateurs nationaux : **{kpis['students']:,} élèves**, "
            f"**{kpis['teachers']} enseignants**, **{kpis['schools']} écoles**. "
            f"Ratio élèves/enseignant : **{kpis['studentsPerTeacher']}**."
        )
    if any(kw in msg for kw in ["région", "regions", "territoire"]):
        regs = await _tool_get_regions_breakdown(session, user)
        tools_used.append("get_regions_breakdown")
        parts.append("🗺 Répartition par région : " + ", ".join(
            f"{r['region']} ({r['schools']} écoles)" for r in regs
        ))
    if any(kw in msg for kw in ["risque", "ratio", "surcharge", "tension"]):
        risk = await _tool_find_at_risk_schools(session, user, threshold=45, limit=5)
        tools_used.append("find_at_risk_schools")
        parts.append(
            "⚠ Top 5 écoles en surcharge : " + " · ".join(
                f"{r['school']} (ratio {r['ratio']})" for r in risk[:5]
            )
        )
    if any(kw in msg for kw in ["décrochage", "absent", "absentéisme"]):
        do = await _tool_count_dropout_critical(session, user)
        tools_used.append("count_dropout_critical")
        parts.append(
            f"🚨 **{do['criticalCount']} élèves** en risque critique de "
            f"décrochage (sur {do['totalAnalyzed']} analysés sur 30 jours)."
        )

    if not parts:
        parts.append(
            "Je peux répondre sur : indicateurs nationaux, répartition régionale, "
            "écoles en surcharge, élèves en risque de décrochage. "
            "Reformule ta question avec un de ces sujets ou configure "
            "ANTHROPIC_API_KEY pour une assistance LLM complète."
        )

    return ChatResponse(
        reply="\n\n".join(parts),
        toolsUsed=tools_used,
    )
