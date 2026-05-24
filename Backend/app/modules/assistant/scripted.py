"""Module 10 — Assistant : mode "scripted fallback".

Quand ``ANTHROPIC_API_KEY`` n'est pas configurée (CI, dev offline, panne
Anthropic), on dégrade vers un mini moteur regex : chaque pattern matche
une intention et délègue à un tool back-end (les mêmes que ceux exposés
au LLM, donc RBAC garanti).

Pourquoi pas un mode "503 sorry" ?
---------------------------------
1. Les démos commerciales doivent toujours fonctionner sans connexion
   internet.
2. Les tests CI peuvent valider l'intégralité du pipeline tool-use sans
   payer un appel Anthropic et sans masquer des bugs liés à la
   sérialisation JSON des tool outputs.

Le format de réponse imite le LLM : prose courte en français, chiffres en
**gras**, mention explicite du tool utilisé (auditabilité).
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assistant.tools import execute_tool

if TYPE_CHECKING:
    from app.modules.auth.models import User


# ---------------------------------------------------------------------------
# Pattern → tool args
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScriptedPattern:
    """Un pattern regex + le tool à appeler + le formateur de réponse."""

    regex: re.Pattern[str]
    tool_name: str
    args_from_match: Callable[[re.Match[str]], dict[str, Any]]
    formatter: Callable[[dict[str, Any]], str]


def _strip_accents(text: str) -> str:
    """Normalise les accents pour matcher 'élèves' aussi bien que 'eleves'."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )


def _fmt_count_students(out: dict[str, Any]) -> str:
    if "error" in out:
        return f"Erreur : {out['error']}"
    n = out["count"]
    gender = out["filters"].get("gender")
    suffix = f" ({gender.lower()})" if gender else ""
    return f"Il y a **{n}** élève(s){suffix} dans votre périmètre."


def _fmt_count_schools(out: dict[str, Any]) -> str:
    if "error" in out:
        return f"Erreur : {out['error']}"
    return f"**{out['count']}** école(s) dans votre périmètre."


def _fmt_list_schools_without_teacher(out: dict[str, Any]) -> str:
    if "error" in out:
        return f"Erreur : {out['error']}"
    schools = out.get("schools", [])
    if not schools:
        return "Aucune école sans enseignant détectée."
    lines = ", ".join(f"{s['name']} ({s['code']})" for s in schools[:10])
    return f"**{len(schools)}** école(s) sans enseignant. Aperçu : {lines}."


def _fmt_attendance_rate(out: dict[str, Any]) -> str:
    if "error" in out:
        return f"Erreur : {out['error']}"
    pct = out["rate"] * 100
    return (
        f"Taux de présence : **{pct:.1f}%** "
        f"(du {out['dateFrom']} au {out['dateTo']}, "
        f"{out['present']} présents sur {out['total']} enregistrements)."
    )


def _fmt_at_risk(out: dict[str, Any]) -> str:
    if "error" in out:
        return f"Erreur : {out['error']}"
    n = out["count"]
    if n == 0:
        return f"Aucun élève au niveau {out['level']}."
    names = ", ".join(
        f"{s['firstName']} {s['lastName']}" for s in out["students"][:5]
    )
    more = f" (et {n - 5} autres)" if n > 5 else ""
    return f"**{n}** élève(s) à risque {out['level']} : {names}{more}."


# Patterns ordonnés du plus spécifique au plus général.
PATTERNS: tuple[ScriptedPattern, ...] = (
    ScriptedPattern(
        regex=re.compile(r"sans\s+enseignant|sans\s+prof", re.IGNORECASE),
        tool_name="list_schools_without_teacher",
        args_from_match=lambda m: {},
        formatter=_fmt_list_schools_without_teacher,
    ),
    ScriptedPattern(
        regex=re.compile(
            r"taux\s+(de\s+)?presence|presence\s+rate|absenteisme",
            re.IGNORECASE,
        ),
        tool_name="get_attendance_rate",
        args_from_match=lambda m: {},
        formatter=_fmt_attendance_rate,
    ),
    ScriptedPattern(
        regex=re.compile(
            r"(eleves?\s+a\s+risque|risque\s+(d'?)?abandon|decroch)",
            re.IGNORECASE,
        ),
        tool_name="get_at_risk_students",
        args_from_match=lambda m: {"level": "HIGH"},
        formatter=_fmt_at_risk,
    ),
    ScriptedPattern(
        regex=re.compile(
            r"(combien|nombre)\s+d[\s']?\s*ecoles?", re.IGNORECASE,
        ),
        tool_name="count_schools",
        args_from_match=lambda m: {},
        formatter=_fmt_count_schools,
    ),
    ScriptedPattern(
        regex=re.compile(
            r"(combien|nombre)\s+d[\s']?\s*eleves?", re.IGNORECASE,
        ),
        tool_name="count_students",
        args_from_match=lambda m: {},
        formatter=_fmt_count_students,
    ),
)


HELP_MESSAGE = (
    "Je suis en mode déconnecté (clé API Anthropic non configurée). "
    "Je peux répondre à : combien d'élèves, combien d'écoles, écoles "
    "sans enseignant, taux de présence, élèves à risque d'abandon. "
    "Reformulez votre question avec un de ces sujets, ou demandez à "
    "votre administrateur de configurer ANTHROPIC_API_KEY pour une "
    "assistance complète."
)


async def run_scripted(
    user_input: str,
    user: "User",
    session: AsyncSession,
) -> tuple[str, str | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Match le premier pattern qui colle et exécute le tool associé.

    Retourne ``(reply_text, tool_name_or_None, tool_input, tool_output)``.

    Si aucun pattern ne matche, retourne un message d'aide et aucun tool.
    Le caller persiste alors :
    * le message utilisateur (role=user),
    * optionnellement le message tool (si tool_name non-None),
    * le message assistant (role=assistant) avec reply_text.
    """
    normalised = _strip_accents(user_input)
    for pattern in PATTERNS:
        m = pattern.regex.search(normalised)
        if m is None:
            continue
        args = pattern.args_from_match(m)
        out = await execute_tool(pattern.tool_name, args, user, session)
        reply = pattern.formatter(out)
        return reply, pattern.tool_name, args, out

    return HELP_MESSAGE, None, None, None


__all__ = ["HELP_MESSAGE", "PATTERNS", "run_scripted"]
