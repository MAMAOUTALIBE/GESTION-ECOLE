"""Module 10 — Assistant LLM (Claude + tool use, scripted fallback).

Endpoints :

* ``POST   /api/assistant/conversations``                  — crée une conv.
* ``GET    /api/assistant/conversations``                  — liste les conv du user.
* ``GET    /api/assistant/conversations/{id}/messages``    — historique.
* ``POST   /api/assistant/conversations/{id}/messages``    — envoie un message.
* ``DELETE /api/assistant/conversations/{id}``             — supprime (owner ou admin).

RBAC :
* L'accès aux endpoints est restreint aux rôles ``ASSISTANT_ROLES``
  (admins + inspecteurs). Les rôles SCHOOL_DIRECTOR sont autorisés pour
  permettre les tests RBAC sur le scope école.
* Pour chaque conversation : owner OR admin (vérifié dans le service).
* Les TOOLS exécutés portent eux-mêmes le scope territorial du user
  (cf. ``app.modules.assistant.tools._user_scope``) → un directeur d'école
  ne peut JAMAIS interroger un autre établissement même via un prompt
  injection.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.modules.assistant.schemas import (
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationRead,
    MessageListResponse,
    MessageRead,
    MessageSendRequest,
    SendMessageResponse,
)
from app.modules.assistant.service import AssistantService
from app.modules.auth.models import User
from app.shared.deps import DbSession, get_current_user
from app.shared.enums import UserRole
from app.shared.permissions import require_roles

router = APIRouter(tags=["assistant"])


ASSISTANT_ROLES = (
    UserRole.NATIONAL_ADMIN,
    UserRole.MINISTRY_ADMIN,
    UserRole.REGIONAL_ADMIN,
    UserRole.INSPECTOR,
    UserRole.PREFECTURE_ADMIN,
    UserRole.SUB_PREFECTURE_ADMIN,
    UserRole.SCHOOL_DIRECTOR,
)


def _svc(session: DbSession) -> AssistantService:
    return AssistantService(session)


Svc = Annotated[AssistantService, Depends(_svc)]


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
@router.post(
    "/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*ASSISTANT_ROLES))],
    summary="Crée une nouvelle conversation",
)
async def create_conversation(
    payload: ConversationCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
) -> ConversationRead:
    conv = await service.create_conversation(user, title=payload.title)
    return ConversationRead.model_validate(conv)


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    dependencies=[Depends(require_roles(*ASSISTANT_ROLES))],
    summary="Liste les conversations de l'utilisateur courant",
)
async def list_conversations(
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
) -> ConversationListResponse:
    items = await service.list_conversations(user)
    return ConversationListResponse(
        items=[ConversationRead.model_validate(c) for c in items],
        total=len(items),
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_roles(*ASSISTANT_ROLES))],
    summary="Supprime une conversation (owner ou admin national)",
)
async def delete_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
) -> None:
    await service.delete_conversation(conversation_id, user)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessageListResponse,
    dependencies=[Depends(require_roles(*ASSISTANT_ROLES))],
    summary="Historique des messages d'une conversation",
)
async def list_messages(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
) -> MessageListResponse:
    items = await service.list_messages(conversation_id, user)
    return MessageListResponse(
        items=[MessageRead.model_validate(m) for m in items],
        total=len(items),
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=SendMessageResponse,
    dependencies=[Depends(require_roles(*ASSISTANT_ROLES))],
    summary="Envoie un message et reçoit la réponse de l'assistant",
)
async def send_message(
    conversation_id: str,
    payload: MessageSendRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Svc,
) -> SendMessageResponse:
    user_msg, assistant_msg, tools_used = await service.send_message(
        conversation_id, payload.content, user,
    )
    return SendMessageResponse(
        userMessage=MessageRead.model_validate(user_msg),
        assistantMessage=MessageRead.model_validate(assistant_msg),
        toolsUsed=tools_used,
    )
