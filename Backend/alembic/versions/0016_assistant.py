"""module 10 — assistant LLM : conversations + messages persistés

Revision ID: 0016_assistant
Revises: 0015_anomalies
Create Date: 2026-05-24

Pourquoi ?
----------
Module 10 expose un assistant conversationnel (Claude + tool-use) pour les
agents ministériels. On persiste l'historique pour deux raisons :

1. **Reprise de session** : l'utilisateur peut continuer une conversation
   ouverte la veille. Les messages doivent donc être rejouables côté UI.
2. **Auditabilité** : toute requête à Claude (avec les tools appelés et
   leurs résultats) est tracée. Indispensable pour un MEN qui veut savoir
   quelles données ont été consultées par qui.

Deux tables :

* ``AssistantConversation`` — coquille par utilisateur (titre auto-généré
  depuis la 1ʳᵉ question, modèle utilisé, scope territorial implicite via
  ``userId``).
* ``AssistantMessage`` — append-only. Trois ``role`` distincts :
  ``user`` (input), ``assistant`` (réponse text), ``tool`` (résultat
  d'un tool call avec ``toolName`` / ``toolInput`` / ``toolOutput``).

Indexes
-------
* ``(userId, updatedAt DESC)`` — listing du dashboard "mes conversations".
* ``(conversationId, createdAt)`` — fetch de l'historique d'une conv,
  ordre chronologique.

Downgrade
---------
Drop des deux tables + drop de l'enum ``AssistantMessageRole``. Le module
9 (anomalies) reste intact.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_assistant"
down_revision: str | Sequence[str] | None = "0015_anomalies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ASSISTANT_MESSAGE_ROLE = postgresql.ENUM(
    "user", "assistant", "tool",
    name="AssistantMessageRole", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    ASSISTANT_MESSAGE_ROLE.create(bind, checkfirst=True)

    # ---- AssistantConversation ---------------------------------------
    op.create_table(
        "AssistantConversation",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("userId", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False,
                  server_default="Nouvelle conversation"),
        sa.Column("model", sa.String(length=60), nullable=False,
                  server_default="scripted"),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["userId"], ["User.id"],
            name="fk_AssistantConversation_userId_User",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_AssistantConversation_userId_updatedAt",
        "AssistantConversation",
        ["userId", sa.text('"updatedAt" DESC')],
    )

    # ---- AssistantMessage --------------------------------------------
    op.create_table(
        "AssistantMessage",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("conversationId", sa.String(length=30), nullable=False),
        sa.Column("role", ASSISTANT_MESSAGE_ROLE, nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("toolName", sa.String(length=80), nullable=True),
        sa.Column(
            "toolInput", postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "toolOutput", postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column("tokensIn", sa.Integer(), nullable=True),
        sa.Column("tokensOut", sa.Integer(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["conversationId"], ["AssistantConversation.id"],
            name="fk_AssistantMessage_conversationId_AssistantConversation",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_AssistantMessage_conversationId_createdAt",
        "AssistantMessage",
        ["conversationId", "createdAt"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_AssistantMessage_conversationId_createdAt",
        table_name="AssistantMessage",
    )
    op.drop_table("AssistantMessage")
    op.drop_index(
        "ix_AssistantConversation_userId_updatedAt",
        table_name="AssistantConversation",
    )
    op.drop_table("AssistantConversation")

    bind = op.get_bind()
    ASSISTANT_MESSAGE_ROLE.drop(bind, checkfirst=True)
