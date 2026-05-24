"""module 15 — admin / settings plateforme : config runtime + feature flags

Revision ID: 0020_admin_settings
Revises: 0019_sms_ussd
Create Date: 2026-05-24

Pourquoi ?
----------
Module 15 ouvre le paramétrage à chaud de la plateforme : modifier un seuil,
activer un flag, basculer la maintenance — tout cela SANS redéploiement et
SANS migration de schema. C'est le panneau de contrôle "régalien" du
ministère.

Trois tables :

* ``PlatformSetting`` — paramètres clé/valeur typés. ``valueJson`` (JSONB)
  porte n'importe quel type sérialisable (boolean, int, float, string,
  object). ``type`` ('boolean' | 'int' | 'float' | 'string' | 'json')
  permet de valider à l'écriture. Idempotent par ``key`` (UNIQUE).
* ``FeatureFlag`` — drapeaux booléens avec rollout progressif (0..100).
  Le rollout est calculé deterministiquement côté service via
  ``hash(flag_key + user_id) % 100``. Permet du canary à grain user.
* ``SettingChangeLog`` — audit append-only des changements (qui, quand,
  quoi avant / quoi après). ``kind`` (SETTING | FEATURE_FLAG) pour
  pouvoir filtrer dans le panneau admin.

Compatibilité
-------------
Une table ``PlatformSetting`` existait déjà (legacy stub Phase 13bis) avec
des colonnes ``value/category/label/valueType``. Nous la DROP et la
recréons avec le schema cible (les seuls usages connus sont les seeds non
encore wired ; le router stub est réécrit en parallèle).

Indexes
-------
* ``PlatformSetting.key`` UNIQUE — lookup en O(1) côté service + cache key.
* ``FeatureFlag.key`` UNIQUE — idem.
* ``SettingChangeLog.key`` — filtrage par paramètre.
* ``SettingChangeLog.changedAt`` — pagination temporelle.

Downgrade
---------
Drop des trois tables + drop de l'enum ``SettingChangeKind``. La table
legacy ``PlatformSetting`` n'est PAS restaurée (le stub n'est pas
référencé en prod).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020_admin_settings"
down_revision: str | Sequence[str] | None = "0019_sms_ussd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SETTING_CHANGE_KIND = postgresql.ENUM(
    "SETTING", "FEATURE_FLAG",
    name="SettingChangeKind", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()

    # ----- Legacy cleanup : drop the stub table if present -----
    # (Phase 13bis a posé un PlatformSetting "string value" — on remet à plat.)
    op.execute("DROP TABLE IF EXISTS \"PlatformSetting\" CASCADE")

    # ----- Enum -----
    SETTING_CHANGE_KIND.create(bind, checkfirst=True)

    # ----- PlatformSetting -----
    op.create_table(
        "PlatformSetting",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column(
            "type", sa.String(length=20),
            nullable=False, server_default="string",
        ),
        sa.Column(
            "valueJson", postgresql.JSONB(),
            nullable=False, server_default="null",
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updatedById", sa.String(length=30), nullable=True),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("key", name="uq_PlatformSetting_key"),
    )

    # ----- FeatureFlag -----
    op.create_table(
        "FeatureFlag",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ),
        sa.Column(
            "rolloutPercentage", sa.Integer(),
            nullable=False, server_default="0",
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("key", name="uq_FeatureFlag_key"),
        sa.CheckConstraint(
            '"rolloutPercentage" >= 0 AND "rolloutPercentage" <= 100',
            name="ck_FeatureFlag_rollout_range",
        ),
    )

    # ----- SettingChangeLog -----
    op.create_table(
        "SettingChangeLog",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("kind", SETTING_CHANGE_KIND, nullable=False),
        sa.Column("oldValue", postgresql.JSONB(), nullable=True),
        sa.Column("newValue", postgresql.JSONB(), nullable=True),
        sa.Column("changedById", sa.String(length=30), nullable=True),
        sa.Column(
            "changedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_SettingChangeLog_key", "SettingChangeLog", ["key"],
    )
    op.create_index(
        "ix_SettingChangeLog_changedAt", "SettingChangeLog", ["changedAt"],
    )


def downgrade() -> None:
    op.drop_index("ix_SettingChangeLog_changedAt", table_name="SettingChangeLog")
    op.drop_index("ix_SettingChangeLog_key", table_name="SettingChangeLog")
    op.drop_table("SettingChangeLog")
    op.drop_table("FeatureFlag")
    op.drop_table("PlatformSetting")

    bind = op.get_bind()
    SETTING_CHANGE_KIND.drop(bind, checkfirst=True)
