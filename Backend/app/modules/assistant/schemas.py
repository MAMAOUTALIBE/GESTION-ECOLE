"""Module 10 — Assistant LLM : schemas Pydantic.

Exposition API minimaliste : on n'expose JAMAIS ``tokensIn`` / ``tokensOut``
au frontend (info interne de facturation). Le ``content`` est trimé côté
input pour éviter qu'un utilisateur ne pollue le contexte LLM avec des
caractères de contrôle.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.assistant.enums import AssistantMessageRole


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
class ConversationCreateRequest(BaseModel):
    """Création d'une conversation. Titre optionnel — sinon auto-généré
    à partir du 1ʳᵉ message utilisateur."""

    model_config = ConfigDict(str_strip_whitespace=True)
    title: str | None = Field(default=None, max_length=200)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    userId: str
    title: str
    model: str
    createdAt: datetime
    updatedAt: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationRead]
    total: int


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------
class MessageSendRequest(BaseModel):
    """Saisie utilisateur. Limite 1000 caractères : suffit largement pour
    une question de dashboard ; au-delà c'est un usage anormal qui ferait
    exploser le coût LLM."""

    model_config = ConfigDict(str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=1000)


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversationId: str
    role: AssistantMessageRole
    content: str
    toolName: str | None = None
    toolInput: dict[str, Any] | None = None
    toolOutput: dict[str, Any] | list[Any] | str | int | float | bool | None = None
    createdAt: datetime


class MessageListResponse(BaseModel):
    items: list[MessageRead]
    total: int


class SendMessageResponse(BaseModel):
    """Réponse de POST /conversations/{id}/messages.

    On renvoie le message utilisateur ET la réponse assistant en un seul
    payload pour que le frontend n'ait pas à refaire un GET derrière.
    """

    userMessage: MessageRead
    assistantMessage: MessageRead
    toolsUsed: list[str] = Field(default_factory=list)
