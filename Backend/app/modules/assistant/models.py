"""Module 10 — Assistant LLM : modèles SQLAlchemy.

Deux tables :
* ``AssistantConversation`` — une coquille par utilisateur.
* ``AssistantMessage`` — append-only ; rôle ``user`` / ``assistant`` / ``tool``.

Conventions
-----------
* ``content`` est TEXT (peut être long pour une réponse LLM ; pas de limite
  applicative ici — la validation des inputs utilisateur est faite dans le
  schéma Pydantic à 1000 chars).
* ``toolInput`` / ``toolOutput`` sont JSONB pour stocker n'importe quel
  payload retourné par un tool (counts, listes d'écoles, etc.).
* On ne dénormalise PAS le ``schoolId`` ou ``regionId`` dans la conversation :
  c'est l'utilisateur (FK ``userId``) qui porte le scope. Si l'utilisateur
  est désactivé / réaffecté, les conversations restent attachées à son
  compte, et un admin peut toujours les consulter.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.assistant.enums import AssistantMessageRole
from app.shared.base import Base, CreatedAtMixin, TimestampMixin, cuid_pk


class AssistantConversation(Base, TimestampMixin):
    """Une conversation = une "session de chat" pour un utilisateur."""

    __tablename__ = "AssistantConversation"
    __table_args__ = (
        Index(
            "ix_AssistantConversation_userId_updatedAt",
            "userId", "updatedAt",
        ),
    )

    id: Mapped[str] = cuid_pk()
    userId: Mapped[str] = mapped_column(
        String(30), ForeignKey("User.id", ondelete="CASCADE"), nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(200), nullable=False,
        default="Nouvelle conversation",
        server_default="Nouvelle conversation",
    )
    # ``model`` permet de tracer quel back-end a généré les réponses :
    # ``scripted`` quand on tombe sur le fallback, ou ex.
    # ``claude-haiku-4-5-20251001`` / ``claude-sonnet-4-6`` sinon.
    model: Mapped[str] = mapped_column(
        String(60), nullable=False,
        default="scripted",
        server_default="scripted",
    )


class AssistantMessage(Base, CreatedAtMixin):
    """Un message d'une conversation. Append-only."""

    __tablename__ = "AssistantMessage"
    __table_args__ = (
        Index(
            "ix_AssistantMessage_conversationId_createdAt",
            "conversationId", "createdAt",
        ),
    )

    id: Mapped[str] = cuid_pk()
    conversationId: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("AssistantConversation.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[AssistantMessageRole] = mapped_column(
        Enum(
            AssistantMessageRole,
            name="AssistantMessageRole",
            native_enum=True,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default="",
    )

    # Tool-call telemetry — null pour les rôles user/assistant.
    toolName: Mapped[str | None] = mapped_column(String(80), nullable=True)
    toolInput: Mapped[Any | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    toolOutput: Mapped[Any | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )

    # Token accounting — utile pour facturer les départements à l'usage.
    tokensIn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokensOut: Mapped[int | None] = mapped_column(Integer, nullable=True)
