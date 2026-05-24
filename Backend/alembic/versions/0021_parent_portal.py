"""module 18 — portail parent : sessions multi-canal + journal WhatsApp

Revision ID: 0021_parent_portal
Revises: 0020_admin_settings
Create Date: 2026-05-24

Pourquoi ?
----------
Module 18 ouvre une "porte d'entrée parent" multi-canal sur la plateforme :

* **WhatsApp Business** : un parent écrit un message libre ("moyenne",
  "presence", "bulletin") → on parse l'intention → on répond avec les
  données de l'enfant rattaché à son numéro.
* **USSD enrichi** : le menu Module 14 reste, mais on l'étend de
  nouvelles options (bulletins / événements à venir) côté code.
* **Page publique HTML légère** (`/api/parent-portal/parent/{phone_hash}`)
  pour les parents qui ont un smartphone mais pas WhatsApp — affichage
  anonymisé (initiales + classe + dernière moyenne).

Deux tables append-only :

* ``ParentSession`` — une session parent, peu importe le canal. ``phoneNumberHash``
  (SHA-256 hex) sert d'identifiant pseudonyme côté URL publique (on ne
  veut JAMAIS exposer le numéro de téléphone en clair dans une URL).
  ``channel`` distingue WHATSAPP / USSD / WEB. ``expiresAt`` permet
  d'expirer la session après 30 minutes d'inactivité.
* ``WhatsAppMessage`` — journal append-only des messages WhatsApp reçus
  et envoyés. ``messageId`` est unique (l'API Cloud WhatsApp garantit
  l'unicité côté provider) — utile pour idempotency et anti-replay.

Enums
-----
* ``ParentChannel`` (``WHATSAPP`` | ``USSD`` | ``WEB``)
* ``WhatsAppDirection`` (``INBOUND`` | ``OUTBOUND``)
* ``WhatsAppStatus`` (``RECEIVED`` | ``PROCESSED`` | ``SENT`` | ``FAILED``)

Indexes
-------
* ``ParentSession.phoneNumberHash`` — lookup en O(1) côté URL publique.
* ``ParentSession.channel`` — segmentation analytics multi-canal.
* ``ParentSession.expiresAt`` — purge périodique.
* ``WhatsAppMessage.messageId`` UNIQUE — idempotency.
* ``WhatsAppMessage.phoneNumber`` — historique par numéro.

Downgrade
---------
Drop des deux tables + drop des enums.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021_parent_portal"
down_revision: str | Sequence[str] | None = "0020_admin_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PARENT_CHANNEL = postgresql.ENUM(
    "WHATSAPP", "USSD", "WEB",
    name="ParentChannel", create_type=False,
)
WHATSAPP_DIRECTION = postgresql.ENUM(
    "INBOUND", "OUTBOUND",
    name="WhatsAppDirection", create_type=False,
)
WHATSAPP_STATUS = postgresql.ENUM(
    "RECEIVED", "PROCESSED", "SENT", "FAILED",
    name="WhatsAppStatus", create_type=False,
)

_ALL_ENUMS = (PARENT_CHANNEL, WHATSAPP_DIRECTION, WHATSAPP_STATUS)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.create(bind, checkfirst=True)

    # ---------------- ParentSession ----------------
    op.create_table(
        "ParentSession",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("phoneNumberHash", sa.String(length=64), nullable=False),
        sa.Column("channel", PARENT_CHANNEL, nullable=False),
        sa.Column(
            "startedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "lastActivityAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "expiresAt", sa.DateTime(timezone=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ParentSession_phoneNumberHash",
        "ParentSession", ["phoneNumberHash"],
    )
    op.create_index(
        "ix_ParentSession_channel",
        "ParentSession", ["channel"],
    )
    op.create_index(
        "ix_ParentSession_expiresAt",
        "ParentSession", ["expiresAt"],
    )

    # ---------------- WhatsAppMessage ----------------
    op.create_table(
        "WhatsAppMessage",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("direction", WHATSAPP_DIRECTION, nullable=False),
        sa.Column("phoneNumber", sa.String(length=20), nullable=False),
        sa.Column("messageId", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status", WHATSAPP_STATUS,
            nullable=False, server_default="RECEIVED",
        ),
        sa.Column(
            "receivedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("processedAt", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("messageId", name="uq_WhatsAppMessage_messageId"),
    )
    op.create_index(
        "ix_WhatsAppMessage_phoneNumber",
        "WhatsAppMessage", ["phoneNumber"],
    )
    op.create_index(
        "ix_WhatsAppMessage_receivedAt",
        "WhatsAppMessage", ["receivedAt"],
    )


def downgrade() -> None:
    op.drop_index("ix_WhatsAppMessage_receivedAt", table_name="WhatsAppMessage")
    op.drop_index("ix_WhatsAppMessage_phoneNumber", table_name="WhatsAppMessage")
    op.drop_table("WhatsAppMessage")

    op.drop_index("ix_ParentSession_expiresAt", table_name="ParentSession")
    op.drop_index("ix_ParentSession_channel", table_name="ParentSession")
    op.drop_index("ix_ParentSession_phoneNumberHash", table_name="ParentSession")
    op.drop_table("ParentSession")

    bind = op.get_bind()
    for enum_type in reversed(_ALL_ENUMS):
        enum_type.drop(bind, checkfirst=True)
