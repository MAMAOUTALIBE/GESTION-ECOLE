"""module 12 — open data portal : catalog des datasets + audit anonyme

Revision ID: 0018_opendata
Revises: 0017_diplomas
Create Date: 2026-05-24

Pourquoi ?
----------
Module 12 expose un portail public sans authentification destiné aux
journalistes, chercheurs et citoyens. Le portail diffuse 6 datasets
anonymisés (agrégats par école/région — aucun PII), sous licence
ouverte (CC-BY-4.0 par défaut).

Deux tables :

* ``OpendataDataset`` — registry des datasets publiés (titre, description,
  licence, schema JSON, fréquence de rafraîchissement, métadonnées du
  dernier refresh). ``key`` est l'identifiant stable utilisé dans les URLs
  publiques (ex. ``schools_by_region``). UNIQUE.
* ``OpendataDownload`` — audit anonyme : pour chaque téléchargement d'un
  dataset on persiste ``ipHash`` (SHA-256 + salt env), ``format`` (json/csv)
  et ``downloadedAt``. Aucune donnée nominative : pas d'IP en clair, pas
  d'userId. Permet de calculer des KPIs publics (dataset le plus consulté)
  sans violer la vie privée.

Indexes
-------
* ``OpendataDataset.key`` UNIQUE — lookup en O(1) côté router.
* ``OpendataDownload.datasetKey`` — agrégats par dataset (stats publics).
* ``OpendataDownload.downloadedAt`` — fenêtres temporelles (24h/7j/30j).

Downgrade
---------
Drop des deux tables. Pas d'enum natif Postgres (format = String pour
rester extensible sans migration).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018_opendata"
down_revision: str | Sequence[str] | None = "0017_diplomas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "OpendataDataset",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "license", sa.String(length=40),
            nullable=False, server_default="CC-BY-4.0",
        ),
        sa.Column(
            "schemaJsonb", postgresql.JSONB(),
            nullable=False, server_default="{}",
        ),
        sa.Column(
            "refreshFrequency", sa.String(length=40),
            nullable=False, server_default="daily",
        ),
        sa.Column(
            "lastRefreshedAt", sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("recordCount", sa.Integer(), nullable=True),
        sa.Column("sizeKb", sa.Integer(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("key", name="uq_OpendataDataset_key"),
    )

    op.create_table(
        "OpendataDownload",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("datasetKey", sa.String(length=80), nullable=False),
        sa.Column("ipHash", sa.String(length=64), nullable=False),
        sa.Column(
            "format", sa.String(length=10),
            nullable=False, server_default="json",
        ),
        sa.Column(
            "downloadedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_OpendataDownload_datasetKey",
        "OpendataDownload", ["datasetKey"],
    )
    op.create_index(
        "ix_OpendataDownload_downloadedAt",
        "OpendataDownload", ["downloadedAt"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_OpendataDownload_downloadedAt",
        table_name="OpendataDownload",
    )
    op.drop_index(
        "ix_OpendataDownload_datasetKey",
        table_name="OpendataDownload",
    )
    op.drop_table("OpendataDownload")
    op.drop_table("OpendataDataset")
