"""module 14 — SMS / USSD gateway : messages outbound + sessions USSD entrantes

Revision ID: 0019_sms_ussd
Revises: 0018_opendata
Create Date: 2026-05-24

Pourquoi ?
----------
Module 14 ouvre l'accès au système GESTION-EE depuis le réseau cellulaire
classique (sans smartphone) — zone rurale guinéenne, public visé : parents
d'élèves. Deux usages :

* **Outbound SMS** : notifications "bulletin disponible", "enfant absent",
  alertes ministérielles. Provider abstrait (Twilio en prod, Mock en dev).
* **USSD inbound** : menu interactif déclenché par ``*999*CODE#``. Le
  parent compose un code court, le réseau pousse un webhook POST chez
  nous, on répond une string ``CON ...`` (continuer) ou ``END ...``
  (terminer). Permet "Quelle est la moyenne de mon enfant ?",
  "Présence cette semaine", "Vérifier diplôme".

Deux tables (append-only, sauf ``status`` qui change PENDING→SENT→DELIVERED) :

* ``SmsMessage`` — chaque message SMS (OUTBOUND ou INBOUND). ``providerId``
  permet de réconcilier avec les delivery reports asynchrones renvoyés par
  Twilio/Orange.
* ``UssdSession`` — état d'une session USSD interactive, indexée par
  ``sessionId`` (unique) et ``phoneNumber`` (pour rate-limit + historique).

Indexes
-------
* ``SmsMessage.to`` — recherche par destinataire (support).
* ``SmsMessage.status`` — KPIs (delivery rate, throughput).
* ``SmsMessage.createdAt`` — agrégats temporels (rapports quotidiens).
* ``SmsMessage.providerId`` — lookup pour réconciliation callback.
* ``UssdSession.sessionId`` UNIQUE — lookup en O(1) côté handler.
* ``UssdSession.phoneNumber`` — rate-limit + historique par numéro.

Downgrade
---------
Drop des deux tables + drop des enums ``SmsDirection`` et ``SmsStatus``.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0019_sms_ussd"
down_revision: str | Sequence[str] | None = "0018_opendata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SMS_DIRECTION = postgresql.ENUM(
    "OUTBOUND", "INBOUND",
    name="SmsDirection", create_type=False,
)
SMS_STATUS = postgresql.ENUM(
    "PENDING", "SENT", "DELIVERED", "FAILED",
    name="SmsStatus", create_type=False,
)

_ALL_ENUMS = (SMS_DIRECTION, SMS_STATUS)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.create(bind, checkfirst=True)

    # ---------------- SmsMessage ----------------
    op.create_table(
        "SmsMessage",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "direction", SMS_DIRECTION,
            nullable=False, server_default="OUTBOUND",
        ),
        sa.Column("to", sa.String(length=20), nullable=False),
        sa.Column("from", sa.String(length=20), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status", SMS_STATUS,
            nullable=False, server_default="PENDING",
        ),
        sa.Column("providerId", sa.String(length=80), nullable=True),
        sa.Column("errorMessage", sa.Text(), nullable=True),
        sa.Column("actorId", sa.String(length=30), nullable=True),
        sa.Column("deliveredAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_SmsMessage_to", "SmsMessage", ["to"])
    op.create_index("ix_SmsMessage_status", "SmsMessage", ["status"])
    op.create_index("ix_SmsMessage_createdAt", "SmsMessage", ["createdAt"])
    op.create_index("ix_SmsMessage_providerId", "SmsMessage", ["providerId"])

    # ---------------- UssdSession ----------------
    op.create_table(
        "UssdSession",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("sessionId", sa.String(length=80), nullable=False),
        sa.Column("phoneNumber", sa.String(length=20), nullable=False),
        sa.Column("serviceCode", sa.String(length=20), nullable=True),
        sa.Column("lastInput", sa.Text(), nullable=True),
        sa.Column(
            "currentStep", sa.String(length=40),
            nullable=False, server_default="MENU",
        ),
        sa.Column("completedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("sessionId", name="uq_UssdSession_sessionId"),
    )
    op.create_index("ix_UssdSession_phoneNumber", "UssdSession", ["phoneNumber"])
    op.create_index("ix_UssdSession_createdAt", "UssdSession", ["createdAt"])


def downgrade() -> None:
    op.drop_index("ix_UssdSession_createdAt", table_name="UssdSession")
    op.drop_index("ix_UssdSession_phoneNumber", table_name="UssdSession")
    op.drop_table("UssdSession")

    op.drop_index("ix_SmsMessage_providerId", table_name="SmsMessage")
    op.drop_index("ix_SmsMessage_createdAt", table_name="SmsMessage")
    op.drop_index("ix_SmsMessage_status", table_name="SmsMessage")
    op.drop_index("ix_SmsMessage_to", table_name="SmsMessage")
    op.drop_table("SmsMessage")

    bind = op.get_bind()
    for enum_type in reversed(_ALL_ENUMS):
        enum_type.drop(bind, checkfirst=True)
