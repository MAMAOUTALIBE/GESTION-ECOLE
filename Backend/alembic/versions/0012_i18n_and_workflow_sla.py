"""module 6 — i18n templates + workflow SLA + per-user preferred language

Revision ID: 0012_i18n_and_workflow_sla
Revises: 0011_reports_async
Create Date: 2026-05-24

Why?
----
* The ministry's user base spans four languages (French, Pular, Soussou,
  Maninka). We store the user's preference on ``User.preferredLanguage``
  and use it to pick the right notification template at dispatch time.
* Validation requests must respect business-grade SLAs (3 days for school
  registration, 2 days for teacher assignment, 5 days for territorial
  changes). We add ``slaDeadline`` / ``escalatedAt`` / ``escalationLevel``
  to power the cron-driven escalation task in
  ``app.workers.workflow_tasks``.
* Notification templates live in their own ``NotificationTemplate`` table
  keyed by ``(key, language, channel)`` — the catalogue is seeded by the
  application via ``seed_default_templates`` and can be re-applied through
  the admin endpoint.

Downgrade
---------
Reverses each step. Templates are dropped; the language preference goes
back to nothing; SLA bookkeeping disappears. No data is migrated back to
the old columns (we don't keep a history).
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_i18n_and_workflow_sla"
down_revision: str | Sequence[str] | None = "0011_reports_async"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- User.preferredLanguage ----------------
    op.add_column(
        "User",
        sa.Column(
            "preferredLanguage",
            sa.String(length=8),
            nullable=False,
            server_default=sa.text("'fr'"),
        ),
    )

    # ---------------- ValidationRequest SLA columns ----------------
    op.add_column(
        "ValidationRequest",
        sa.Column("slaDeadline", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ValidationRequest",
        sa.Column("escalatedAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ValidationRequest",
        sa.Column(
            "escalationLevel",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # ---------------- NotificationTemplate ----------------
    op.create_table(
        "NotificationTemplate",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column(
            "variables",
            sa.dialects.postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "key", "language", "channel",
            name="uq_NotificationTemplate_key_language_channel",
        ),
    )
    op.create_index(
        "ix_NotificationTemplate_key", "NotificationTemplate", ["key"]
    )


def downgrade() -> None:
    op.drop_index("ix_NotificationTemplate_key", table_name="NotificationTemplate")
    op.drop_table("NotificationTemplate")

    op.drop_column("ValidationRequest", "escalationLevel")
    op.drop_column("ValidationRequest", "escalatedAt")
    op.drop_column("ValidationRequest", "slaDeadline")

    op.drop_column("User", "preferredLanguage")
