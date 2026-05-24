"""module 19 — cockpit ministériel : snapshots quotidiens des KPI agrégés

Revision ID: 0022_cockpit
Revises: 0021_parent_portal
Create Date: 2026-05-24

Pourquoi ?
----------
Module 19 ouvre la surface "cockpit ministériel" : KPI live + briefing
quotidien LLM. Pour comparer un KPI entre J et J-1 (et plus loin J-7 /
J-30), on a besoin d'un historique stocké en base — recalculer les
agrégats à la demande explose la latence côté API.

Une seule table append-only : ``CockpitKpiSnapshot`` — un row par
(snapshotDate, kpiKey, scope[, regionId]). Le service écrit en batch
via la tâche Celery beat quotidienne ``cockpit.snapshot_daily_kpis``
(idempotente : delete + insert sur la même date).

Enums
-----
* ``KpiKey``       — 5 valeurs (STUDENTS_TOTAL, ATTENDANCE_RATE,
  BUDGET_CONSUMPTION, CRITICAL_ANOMALIES_OPEN, ALERTS_OPEN).
* ``CockpitScope`` — NATIONAL | REGIONAL.

Indexes
-------
* ``(snapshotDate, kpiKey)`` : optimisation lecture des séries temporelles
  d'un KPI donné (cas dominant côté UI).
* ``(kpiKey, scope)`` : filtres rapides sur un KPI national vs régional.

Downgrade
---------
Drop table + drop des deux enums.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022_cockpit"
down_revision: str | Sequence[str] | None = "0021_parent_portal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


KPI_KEY = postgresql.ENUM(
    "STUDENTS_TOTAL",
    "ATTENDANCE_RATE",
    "BUDGET_CONSUMPTION",
    "CRITICAL_ANOMALIES_OPEN",
    "ALERTS_OPEN",
    name="KpiKey",
    create_type=False,
)
COCKPIT_SCOPE = postgresql.ENUM(
    "NATIONAL", "REGIONAL",
    name="CockpitScope", create_type=False,
)

_ALL_ENUMS = (KPI_KEY, COCKPIT_SCOPE)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "CockpitKpiSnapshot",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("snapshotDate", sa.Date(), nullable=False),
        sa.Column("kpiKey", KPI_KEY, nullable=False),
        sa.Column(
            "scope", COCKPIT_SCOPE,
            nullable=False, server_default="NATIONAL",
        ),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "regionId", sa.String(length=30),
            sa.ForeignKey("Region.id"), nullable=True,
        ),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_CockpitKpiSnapshot_snapshotDate_kpiKey",
        "CockpitKpiSnapshot",
        ["snapshotDate", "kpiKey"],
    )
    op.create_index(
        "ix_CockpitKpiSnapshot_kpiKey_scope",
        "CockpitKpiSnapshot",
        ["kpiKey", "scope"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_CockpitKpiSnapshot_kpiKey_scope",
        table_name="CockpitKpiSnapshot",
    )
    op.drop_index(
        "ix_CockpitKpiSnapshot_snapshotDate_kpiKey",
        table_name="CockpitKpiSnapshot",
    )
    op.drop_table("CockpitKpiSnapshot")
    bind = op.get_bind()
    for enum_type in reversed(_ALL_ENUMS):
        enum_type.drop(bind, checkfirst=True)
