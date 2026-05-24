"""Module 10 — Assistant LLM (Claude + scripted fallback).

Toujours en mode SCRIPTED FALLBACK (ANTHROPIC_API_KEY supprimée pour la
session) : pas d'appel réseau. On valide :

1. CRUD conversations (create, list, get, delete).
2. send_message dispatch les tools selon les patterns regex.
3. Persistance des trois rôles : user / tool / assistant.
4. RBAC : un SCHOOL_DIRECTOR ne voit que SON école.
5. Rate-limit : 30 messages / heure / user.
6. Owner / admin pour la suppression.
"""
from __future__ import annotations

import os
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.assistant.enums import AssistantMessageRole
from app.modules.assistant.models import (
    AssistantConversation,
    AssistantMessage,
)
from app.modules.assistant.service import RATE_LIMIT_MESSAGES_PER_HOUR
from app.shared.enums import UserRole
from tests.integration import factories

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Make sure no ANTHROPIC_API_KEY leaks into the session — tests MUST run in
# scripted fallback mode.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Territorial + student data
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(loop_scope="session")
async def school_ctx(db_session: AsyncSession) -> dict[str, Any]:
    factories.bind(db_session)
    tree = await factories.make_territorial_tree()
    # 3 élèves dans l'école principale
    students = []
    for _ in range(3):
        s = await factories.StudentFactory.create_async(
            schoolId=tree["school"].id,
        )
        students.append(s)
    return {
        "region": tree["region"],
        "prefecture": tree["prefecture"],
        "subPrefecture": tree["subPrefecture"],
        "school": tree["school"],
        "students": students,
    }


@pytest_asyncio.fixture(loop_scope="session")
async def director_headers(
    auth_headers: Any, school_ctx: dict[str, Any],
) -> dict[str, str]:
    return await auth_headers(
        UserRole.SCHOOL_DIRECTOR,
        regionId=school_ctx["region"].id,
        prefectureId=school_ctx["prefecture"].id,
        subPrefectureId=school_ctx["subPrefecture"].id,
        schoolId=school_ctx["school"].id,
    )


@pytest_asyncio.fixture(loop_scope="session")
async def national_headers(auth_headers: Any) -> dict[str, str]:
    return await auth_headers(UserRole.NATIONAL_ADMIN)


@pytest_asyncio.fixture(loop_scope="session")
async def other_director_headers(
    auth_headers: Any, db_session: AsyncSession,
) -> dict[str, str]:
    factories.bind(db_session)
    other = await factories.make_territorial_tree()
    return await auth_headers(
        UserRole.SCHOOL_DIRECTOR,
        regionId=other["region"].id,
        prefectureId=other["prefecture"].id,
        subPrefectureId=other["subPrefecture"].id,
        schoolId=other["school"].id,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _create_conv(
    client: AsyncClient, headers: dict[str, str],
) -> str:
    r = await client.post(
        "/api/assistant/conversations", headers=headers, json={},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ===========================================================================
# 1. Creation
# ===========================================================================
@pytest.mark.asyncio
async def test_create_conversation_returns_id(
    client: AsyncClient, director_headers: dict[str, str],
) -> None:
    r = await client.post(
        "/api/assistant/conversations",
        headers=director_headers,
        json={"title": "Mon premier chat"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"]
    assert body["title"] == "Mon premier chat"
    # En mode scripted (pas de clé), model = "scripted"
    assert body["model"] == "scripted"


# ===========================================================================
# 2-4. Tool dispatch via send_message
# ===========================================================================
@pytest.mark.asyncio
async def test_send_message_count_students_returns_number(
    client: AsyncClient, director_headers: dict[str, str],
    school_ctx: dict[str, Any],
) -> None:
    conv_id = await _create_conv(client, director_headers)
    r = await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers,
        json={"content": "Combien d'élèves dans mon école ?"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["toolsUsed"] == ["count_students"]
    # 3 élèves créés dans school_ctx
    assert "**3**" in body["assistantMessage"]["content"]


@pytest.mark.asyncio
async def test_send_message_count_schools_returns_number(
    client: AsyncClient, director_headers: dict[str, str],
) -> None:
    conv_id = await _create_conv(client, director_headers)
    r = await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers,
        json={"content": "Combien d'écoles au total ?"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["toolsUsed"] == ["count_schools"]
    # Un directeur scopé à son école → count == 1
    assert "**1**" in body["assistantMessage"]["content"]


@pytest.mark.asyncio
async def test_send_message_at_risk_students(
    client: AsyncClient, director_headers: dict[str, str],
) -> None:
    conv_id = await _create_conv(client, director_headers)
    r = await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers,
        json={"content": "Quels élèves à risque d'abandon ?"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["toolsUsed"] == ["get_at_risk_students"]
    # Pas de prédictions seedées → "Aucun élève au niveau HIGH"
    assert "Aucun élève" in body["assistantMessage"]["content"]


# ===========================================================================
# 5. Persistance des messages
# ===========================================================================
@pytest.mark.asyncio
async def test_send_message_persists_user_and_assistant_messages(
    client: AsyncClient, db_session: AsyncSession,
    director_headers: dict[str, str],
) -> None:
    conv_id = await _create_conv(client, director_headers)
    await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers,
        json={"content": "combien d'élèves"},
    )
    # Récupère tous les messages de la conv depuis la DB
    stmt = select(AssistantMessage).where(
        AssistantMessage.conversationId == conv_id,
    ).order_by(AssistantMessage.createdAt.asc())
    msgs = list((await db_session.execute(stmt)).scalars())
    roles = [m.role for m in msgs]
    assert AssistantMessageRole.USER in roles
    assert AssistantMessageRole.TOOL in roles
    assert AssistantMessageRole.ASSISTANT in roles


# ===========================================================================
# 6. Mode scripted explicitement quand pas de clé
# ===========================================================================
@pytest.mark.asyncio
async def test_scripted_fallback_no_api_key(
    client: AsyncClient, director_headers: dict[str, str],
) -> None:
    # On vérifie côté env runtime (l'autouse fixture supprime la clé).
    assert "ANTHROPIC_API_KEY" not in os.environ

    conv_id = await _create_conv(client, director_headers)
    r = await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers,
        json={"content": "combien d'élèves dans la base ?"},
    )
    assert r.status_code == 200, r.text
    # En scripted, on ne fait qu'UN tool call (le 1ʳᵉ qui matche).
    assert r.json()["toolsUsed"] == ["count_students"]


# ===========================================================================
# 7. Isolation user — list ne montre QUE les conv du current user
# ===========================================================================
@pytest.mark.asyncio
async def test_list_conversations_returns_only_own(
    client: AsyncClient, director_headers: dict[str, str],
    other_director_headers: dict[str, str],
) -> None:
    # Le user A crée 2 conversations
    await _create_conv(client, director_headers)
    await _create_conv(client, director_headers)
    # Le user B crée 1
    await _create_conv(client, other_director_headers)

    r_a = await client.get(
        "/api/assistant/conversations", headers=director_headers,
    )
    assert r_a.status_code == 200
    assert r_a.json()["total"] == 2

    r_b = await client.get(
        "/api/assistant/conversations", headers=other_director_headers,
    )
    assert r_b.status_code == 200
    assert r_b.json()["total"] == 1


# ===========================================================================
# 8. Suppression — owner ou admin uniquement
# ===========================================================================
@pytest.mark.asyncio
async def test_delete_conversation_requires_ownership(
    client: AsyncClient, director_headers: dict[str, str],
    other_director_headers: dict[str, str],
    national_headers: dict[str, str],
) -> None:
    conv_id = await _create_conv(client, director_headers)

    # Un autre directeur ne peut PAS supprimer
    r = await client.delete(
        f"/api/assistant/conversations/{conv_id}",
        headers=other_director_headers,
    )
    assert r.status_code == 403, r.text

    # Un admin national peut supprimer
    r = await client.delete(
        f"/api/assistant/conversations/{conv_id}",
        headers=national_headers,
    )
    assert r.status_code == 204, r.text


# ===========================================================================
# 9. Rate-limit — 30 messages/heure
# ===========================================================================
@pytest.mark.asyncio
async def test_rate_limit_blocks_after_30_messages(
    client: AsyncClient, director_headers: dict[str, str],
) -> None:
    conv_id = await _create_conv(client, director_headers)
    payload = {"content": "combien d'écoles ?"}

    # Les RATE_LIMIT_MESSAGES_PER_HOUR premiers passent
    for _i in range(RATE_LIMIT_MESSAGES_PER_HOUR):
        r = await client.post(
            f"/api/assistant/conversations/{conv_id}/messages",
            headers=director_headers, json=payload,
        )
        assert r.status_code == 200, f"itération {_i}: {r.text}"

    # Le suivant est bloqué
    r = await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers, json=payload,
    )
    assert r.status_code == 429, r.text
    body = r.json()
    assert body["code"] == "rate_limited"


# ===========================================================================
# 10. RBAC — un directeur ne voit que les élèves de son école
# ===========================================================================
@pytest.mark.asyncio
async def test_tool_execution_respects_rbac_scope(
    client: AsyncClient, db_session: AsyncSession,
    director_headers: dict[str, str], school_ctx: dict[str, Any],
) -> None:
    # On ajoute 5 élèves dans une AUTRE école (region/prefecture différentes).
    factories.bind(db_session)
    other = await factories.make_territorial_tree()
    for _ in range(5):
        await factories.StudentFactory.create_async(schoolId=other["school"].id)
    await db_session.flush()

    conv_id = await _create_conv(client, director_headers)
    r = await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers,
        json={"content": "combien d'élèves au total ?"},
    )
    assert r.status_code == 200, r.text
    # Le directeur scopé à son école = 3 élèves (school_ctx), pas 8.
    assert "**3**" in r.json()["assistantMessage"]["content"]
    assert "**8**" not in r.json()["assistantMessage"]["content"]


# ===========================================================================
# 11. Pattern non reconnu — message d'aide
# ===========================================================================
@pytest.mark.asyncio
async def test_unknown_pattern_returns_helpful_message(
    client: AsyncClient, director_headers: dict[str, str],
) -> None:
    conv_id = await _create_conv(client, director_headers)
    r = await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers,
        json={"content": "Quel est le sens de la vie ?"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["toolsUsed"] == []
    # Le HELP_MESSAGE doit apparaître
    assert "mode déconnecté" in body["assistantMessage"]["content"]


# ===========================================================================
# 12. Persistance avec rôles distincts
# ===========================================================================
@pytest.mark.asyncio
async def test_messages_stored_with_role(
    client: AsyncClient, db_session: AsyncSession,
    director_headers: dict[str, str],
) -> None:
    conv_id = await _create_conv(client, director_headers)
    await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers,
        json={"content": "combien d'élèves"},
    )
    r = await client.get(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # On attend 3 messages : user → tool → assistant.
    assert body["total"] == 3
    roles = [m["role"] for m in body["items"]]
    assert roles == ["user", "tool", "assistant"]
    # Le message tool doit porter toolName + toolOutput non null.
    tool_msg = body["items"][1]
    assert tool_msg["toolName"] == "count_students"
    assert tool_msg["toolOutput"]["count"] == 3


# ===========================================================================
# 13. Limite input — 1000 chars max
# ===========================================================================
@pytest.mark.asyncio
async def test_message_input_max_length(
    client: AsyncClient, director_headers: dict[str, str],
) -> None:
    conv_id = await _create_conv(client, director_headers)
    too_long = "a" * 1001
    r = await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers, json={"content": too_long},
    )
    # Pydantic renvoie 422 sur violation de Field(max_length=1000)
    assert r.status_code == 422, r.text

    # Pile 1000 chars passe
    ok = "combien d'élèves " + "x" * (1000 - len("combien d'élèves "))
    r = await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        headers=director_headers, json={"content": ok},
    )
    assert r.status_code == 200, r.text


# ===========================================================================
# 14. Auth — endpoints messages exigent l'authentification
# ===========================================================================
@pytest.mark.asyncio
async def test_messages_endpoint_requires_auth(
    client: AsyncClient, director_headers: dict[str, str],
) -> None:
    conv_id = await _create_conv(client, director_headers)
    # Sans Authorization header → 401 (UnauthorizedError)
    r = await client.get(
        f"/api/assistant/conversations/{conv_id}/messages",
    )
    assert r.status_code == 401, r.text

    r = await client.post(
        f"/api/assistant/conversations/{conv_id}/messages",
        json={"content": "test"},
    )
    assert r.status_code == 401, r.text


# Silence linters keeping a referenced symbol around for parity with the
# other module test packs (e.g. AssistantConversation is used implicitly
# via DB integrity checks above).
_ = AssistantConversation
