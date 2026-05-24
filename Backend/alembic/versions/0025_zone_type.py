"""module 1C — Segmentation urbain/rural/péri-urbain

Revision ID: 0025_zone_type
Revises: 0024_gender_parity
Create Date: 2026-05-24

Pourquoi ?
----------
Objectif gouvernemental "corriger les disparités urbain vs rural".
Sans segmentation, impossible de mesurer l'écart de scolarisation, le
GPI urbain vs rural, ou le ratio enseignants/élèves par type de zone.

Stratégie de modélisation : déclaratif + override
-------------------------------------------------
* La source de vérité est ``SubPrefecture.defaultZoneType`` — l'INS et
  le MEN cataloguent une fois par sous-préfecture (~~340 lignes pays).
* Les écoles héritent la zone de leur sous-préfecture par défaut
  (``School.zoneType`` NULL = hérité).
* L'override école est nullable pour les cas frontaliers
  (école dans un quartier urbain d'une sous-préf rurale dominante).

Pourquoi pas un calcul GPS ?
* Pas de cadastre fiable en Guinée — un point GPS ne dit pas si l'école
  est en zone urbaine administrative.
* La nomenclature statistique nationale est déclarative ; respecter
  l'INS évite des écarts avec les publications officielles.

Migration sans valeur initiale
------------------------------
* ``SubPrefecture.defaultZoneType`` est NOT NULL DEFAULT 'RURAL' — c'est
  la valeur la plus fréquente du pays (~~70%). L'INS pourra raffiner via
  ``PUT /api/territory/sub-prefectures/{id}/zone-type``.
* ``School.zoneType`` est NULL par défaut (hérite). L'override est posé
  manuellement par NATIONAL/MINISTRY/REGIONAL.

Index
-----
* ``ix_SubPrefecture_defaultZoneType`` — agrégats nationaux par zone.
* ``ix_School_zoneType`` partiel (WHERE zoneType IS NOT NULL) — la majorité
  des écoles ne posent pas d'override, donc l'index reste compact.

Downgrade
---------
Drop colonnes + drop enum ZoneType.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025_zone_type"
down_revision: str | Sequence[str] | None = "0024_gender_parity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ZONE_TYPE = postgresql.ENUM(
    "URBAN",
    "RURAL",
    "PERI_URBAN",
    name="ZoneType",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    ZONE_TYPE.create(bind, checkfirst=True)

    # Module 9 — ajoute URBAN_RURAL_GPI_GAP à l'enum AnomalyType existant.
    # ``ALTER TYPE … ADD VALUE`` est supporté en transaction depuis Postgres
    # 12 (la prod tourne en 16). ``IF NOT EXISTS`` rend l'opération
    # idempotente — au cas où la migration aurait été rejouée.
    op.execute(
        "ALTER TYPE \"AnomalyType\" "
        "ADD VALUE IF NOT EXISTS 'URBAN_RURAL_GPI_GAP'"
    )

    # SubPrefecture.defaultZoneType — NOT NULL DEFAULT 'RURAL'.
    # On ajoute la colonne avec un default serveur pour que les ~340 sous-préfs
    # existantes soient initialisées immédiatement, puis on retire le default
    # serveur (la valeur sera désormais posée explicitement par le service).
    op.add_column(
        "SubPrefecture",
        sa.Column(
            "defaultZoneType",
            ZONE_TYPE,
            nullable=False,
            server_default="RURAL",
        ),
    )
    op.alter_column(
        "SubPrefecture",
        "defaultZoneType",
        server_default=None,
    )

    # School.zoneType — NULL = hérite de la sous-préfecture.
    op.add_column(
        "School",
        sa.Column("zoneType", ZONE_TYPE, nullable=True),
    )

    # Index sur SubPrefecture.defaultZoneType — agrégats nationaux.
    op.create_index(
        "ix_SubPrefecture_defaultZoneType",
        "SubPrefecture",
        ["defaultZoneType"],
    )

    # Index partiel sur School.zoneType — seulement les overrides effectifs
    # (gros gain de taille : la majorité des écoles n'override pas).
    op.create_index(
        "ix_School_zoneType",
        "School",
        ["zoneType"],
        postgresql_where=sa.text('"zoneType" IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index("ix_School_zoneType", table_name="School")
    op.drop_index("ix_SubPrefecture_defaultZoneType", table_name="SubPrefecture")
    op.drop_column("School", "zoneType")
    op.drop_column("SubPrefecture", "defaultZoneType")
    bind = op.get_bind()
    ZONE_TYPE.drop(bind, checkfirst=True)
