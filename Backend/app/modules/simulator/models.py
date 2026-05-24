"""Module 3B — Modèle SQLAlchemy du simulateur what-if.

Une seule table : ``SimulationScenario``.

Pourquoi persister ?
--------------------
Le simulateur est read-only (n'écrit jamais dans ``School``) mais on
persiste le scénario lui-même pour :

* permettre au planificateur de retrouver et rejouer ses hypothèses ;
* auditer les décisions (qui a simulé quoi, quand) — exigence IIPE pour
  les décisions structurantes ;
* permettre une revue collégiale (le cabinet peut consulter le scénario
  d'un REGIONAL_ADMIN).

Les écoles fictives (``CREATE_SCHOOL`` / ``MERGE_SCHOOLS``) restent dans
``scenarioJson`` (JSONB) — elles n'apparaissent jamais dans la table
``School`` officielle.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.simulator.enums import ScenarioStatus
from app.shared.base import Base, CreatedAtMixin, cuid_pk

if TYPE_CHECKING:
    from app.modules.academics.models import SchoolYear
    from app.modules.auth.models import User


class SimulationScenario(Base, CreatedAtMixin):
    """Scénario de simulation what-if (persisté en JSONB pour audit).

    Champs structurés :

    * ``id`` / ``name`` / ``description`` : descriptifs.
    * ``createdById`` : auteur du scénario (FK User).
    * ``createdAt`` : héritée de ``CreatedAtMixin``.
    * ``status`` : ScenarioStatus (DRAFT → COMPUTED → ARCHIVED).
    * ``baselineSchoolYearId`` : SchoolYear de référence pour la photo
      du réseau (FK SchoolYear).
    * ``scenarioJson`` : payload des opérations (forme validée par les
      schemas Pydantic ``ScenarioCreate.operations``).
    * ``impactJson`` : rempli par ``compute_scenario`` ; ``None`` tant
      qu'en DRAFT.
    * ``computedAt`` : ``None`` tant qu'en DRAFT ; horodatage du dernier
      ``compute_scenario`` sinon (un recompute remplace).
    """

    __tablename__ = "SimulationScenario"
    __table_args__ = (
        Index(
            "ix_SimulationScenario_createdBy_createdAt",
            "createdById", "createdAt",
        ),
        Index(
            "ix_SimulationScenario_status",
            "status",
        ),
    )

    id: Mapped[str] = cuid_pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(
        String(500), nullable=True,
    )
    createdById: Mapped[str] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=False,
    )
    status: Mapped[ScenarioStatus] = mapped_column(
        Enum(
            ScenarioStatus,
            name="SimulationScenarioStatus",
            native_enum=True,
        ),
        nullable=False,
        default=ScenarioStatus.DRAFT,
        server_default="DRAFT",
    )
    baselineSchoolYearId: Mapped[str] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=False,
    )
    # JSONB sur PostgreSQL, JSON ailleurs (cohérent avec opendata/
    # anomalies/projections).
    scenarioJson: Mapped[Any] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
    )
    impactJson: Mapped[Any | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    computedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    createdBy: Mapped["User"] = relationship(lazy="raise")
    baselineSchoolYear: Mapped["SchoolYear"] = relationship(lazy="raise")


__all__ = ["SimulationScenario"]
