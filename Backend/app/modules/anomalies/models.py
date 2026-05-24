"""Module 9 — Anomalies detection : modèle SQLAlchemy.

Une seule table ``AnomalyDetection`` (append-only avec colonnes de revue).

Conventions
-----------
* ``evidence`` JSONB stocke les champs exacts qui ont déclenché l'anomalie
  (ex. ``{"score": 25, "max": 20, "assessmentId": "..."}``). Permet au
  directeur d'école de comprendre la détection en un coup d'œil sans avoir
  à rejoindre la donnée source.
* ``status`` reste ``PENDING`` jusqu'à révision humaine. ``reviewedAt`` /
  ``reviewedById`` / ``reviewNote`` se remplissent à ce moment-là.
* ``schoolId`` / ``regionId`` dénormalisés pour le scope territorial — un
  agent SCHOOL_DIRECTOR ne voit que les anomalies de son école sans avoir
  à rejoindre Student/School à chaque listing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.anomalies.enums import (
    AnomalySeverity,
    AnomalyStatus,
    AnomalyType,
)
from app.shared.base import Base, CreatedAtMixin, cuid_pk


class AnomalyDetection(Base, CreatedAtMixin):
    """Une anomalie détectée sur une entité métier (élève, école, classe…).

    Une nouvelle ligne est insérée à chaque run de détection. La
    déduplication n'est PAS faite en DB (volontairement : on veut une trace
    temporelle pour mesurer la persistance d'un signal). Le service de
    listing peut filtrer pour ne montrer que la dernière occurrence par
    ``(entityType, entityId, type)``.
    """

    __tablename__ = "AnomalyDetection"
    __table_args__ = (
        Index(
            "ix_AnomalyDetection_status_severity",
            "status", "severity",
        ),
        Index(
            "ix_AnomalyDetection_entityType_entityId",
            "entityType", "entityId",
        ),
        Index(
            "ix_AnomalyDetection_schoolId_detectedAt",
            "schoolId", "detectedAt",
        ),
    )

    id: Mapped[str] = cuid_pk()
    type: Mapped[AnomalyType] = mapped_column(
        Enum(AnomalyType, name="AnomalyType", native_enum=True),
        nullable=False,
    )
    severity: Mapped[AnomalySeverity] = mapped_column(
        Enum(AnomalySeverity, name="AnomalySeverity", native_enum=True),
        nullable=False,
    )
    status: Mapped[AnomalyStatus] = mapped_column(
        Enum(AnomalyStatus, name="AnomalyStatus", native_enum=True),
        default=AnomalyStatus.PENDING,
        nullable=False,
        server_default="PENDING",
    )

    # Référence souple : pas de FK car l'entityId peut viser n'importe
    # quelle table (Student, ClassRoom, School…). On garde le scope via
    # schoolId/regionId dénormalisés (FK ci-dessous).
    entityType: Mapped[str] = mapped_column(String(40), nullable=False)
    entityId: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[Any] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )

    schoolId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=True,
    )
    regionId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("Region.id"), nullable=True,
    )

    detectedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    reviewedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    reviewedById: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("User.id"), nullable=True,
    )
    reviewNote: Mapped[str | None] = mapped_column(Text, nullable=True)
