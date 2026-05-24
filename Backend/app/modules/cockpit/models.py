"""Module 19 — Modèles SQLAlchemy du cockpit ministériel.

Une seule table append-only : ``CockpitKpiSnapshot`` — un snapshot
quotidien des KPI agrégés (national + breakdown régional optionnel).
Source de vérité historique pour les comparaisons temporelles (variation
J-1 / J-7) sans avoir à re-scanner toutes les tables métier à chaque
appel.

Conventions
-----------
* ``value`` est un FLOAT — la majorité des KPI sont des ratios ou des
  compteurs entiers stockés en float (compromis simplicité / portabilité).
* ``metadata`` JSONB stocke les champs additionnels (par exemple
  ``{"unit": "percent", "n_schools": 12345}``). On évite ainsi d'ajouter
  des colonnes ad hoc à chaque nouveau KPI.
* ``regionId`` est nullable : NULL = portée nationale (scope NATIONAL),
  renseigné = portée régionale (scope REGIONAL).
* Index ``(snapshotDate DESC, kpiKey)`` : optimise la lecture des séries
  temporelles d'un KPI donné (cas dominant côté lecture).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.cockpit.enums import CockpitScope, KpiKey
from app.shared.base import Base, CreatedAtMixin, cuid_pk


class CockpitKpiSnapshot(Base, CreatedAtMixin):
    """Snapshot quotidien d'un KPI cockpit (append-only)."""

    __tablename__ = "CockpitKpiSnapshot"
    __table_args__ = (
        Index(
            "ix_CockpitKpiSnapshot_snapshotDate_kpiKey",
            "snapshotDate", "kpiKey",
        ),
        Index(
            "ix_CockpitKpiSnapshot_kpiKey_scope",
            "kpiKey", "scope",
        ),
    )

    id: Mapped[str] = cuid_pk()
    snapshotDate: Mapped[date] = mapped_column(Date, nullable=False)
    kpiKey: Mapped[KpiKey] = mapped_column(
        Enum(KpiKey, name="KpiKey", native_enum=True),
        nullable=False,
    )
    scope: Mapped[CockpitScope] = mapped_column(
        Enum(CockpitScope, name="CockpitScope", native_enum=True),
        default=CockpitScope.NATIONAL,
        nullable=False,
        server_default="NATIONAL",
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    extra: Mapped[Any] = mapped_column(
        "metadata",  # nom colonne DB (évite la collision avec SQLAlchemy.metadata)
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    regionId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Region.id"), nullable=True,
    )


__all__ = ["CockpitKpiSnapshot"]
