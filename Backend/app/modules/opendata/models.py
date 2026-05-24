"""Module 12 — Open Data portal : modèles SQLAlchemy.

Deux tables :

* :class:`OpendataDataset` — catalog des datasets publiés (titre,
  description, licence, schema JSON, fréquence de rafraîchissement,
  métadonnées du dernier refresh). La colonne ``key`` est l'identifiant
  stable utilisé dans les URLs publiques (``/api/opendata/datasets/{key}``)
  et reste UNIQUE.
* :class:`OpendataDownload` — audit append-only des téléchargements. On
  ne stocke JAMAIS l'IP en clair, uniquement son hash SHA-256+salt
  (``app.modules.opendata.anonymization.hash_ip``). Permet de calculer
  des KPIs publics (dataset le plus consulté sur 7 jours) sans violer la
  vie privée des consommateurs.

Pourquoi pas un enum natif pour ``format`` ?
--------------------------------------------
On garde ``String`` plutôt qu'un enum Postgres pour rester extensible —
ajouter un format (xlsx, parquet…) ne nécessitera pas de migration de type.
La validation est faite côté router/service.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, TimestampMixin, cuid_pk


class OpendataDataset(Base, TimestampMixin):
    """Catalog d'un dataset open data exposé publiquement.

    Une ligne par dataset. ``key`` est l'identifiant stable utilisé par
    le router et qui ne change jamais (les URLs publiques doivent rester
    citables dans des articles / publications académiques).
    """

    __tablename__ = "OpendataDataset"
    __table_args__ = (
        UniqueConstraint("key", name="uq_OpendataDataset_key"),
    )

    id: Mapped[str] = cuid_pk()
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    license: Mapped[str] = mapped_column(
        String(40), nullable=False, default="CC-BY-4.0",
        server_default="CC-BY-4.0",
    )

    # JSON Schema décrivant la forme des records exposés. Stocké en JSONB
    # pour permettre des requêtes ad-hoc côté analytics (rare mais utile).
    schemaJsonb: Mapped[Any] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False, default=dict, server_default="{}",
    )

    # Cadence indicative ("daily", "weekly", "monthly", "on_demand").
    # Free-form string : pas d'enum pour rester extensible.
    refreshFrequency: Mapped[str] = mapped_column(
        String(40), nullable=False, default="daily",
        server_default="daily",
    )

    lastRefreshedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    recordCount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sizeKb: Mapped[int | None] = mapped_column(Integer, nullable=True)


class OpendataDownload(Base):
    """Audit anonyme d'un téléchargement.

    Pas de FK vers ``OpendataDataset`` : on garde la trace même si le
    dataset est dépublié plus tard (auditabilité historique). On stocke
    uniquement le ``datasetKey`` (string) et le hash de l'IP — aucune
    information nominative.
    """

    __tablename__ = "OpendataDownload"

    id: Mapped[str] = cuid_pk()
    datasetKey: Mapped[str] = mapped_column(String(80), nullable=False)
    ipHash: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(
        String(10), nullable=False, default="json",
        server_default="json",
    )
    downloadedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
