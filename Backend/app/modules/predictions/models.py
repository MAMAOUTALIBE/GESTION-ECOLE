"""Module 8 — Predictions ML : modèles SQLAlchemy.

Deux tables :
* ``DropoutPrediction`` — score calculé pour un élève à un moment T.
* ``DropoutModelMetadata`` — registry des modèles entraînés (versioning).

Conventions
-----------
* Les colonnes JSONB ``featuresSnapshot`` / ``metrics`` permettent de tracer
  les données d'entrée du score (auditabilité / fairness) sans avoir à créer
  une colonne par feature (qui changera à chaque évolution de modèle).
* ``riskLevel`` est un enum natif Postgres pour profiter des filtres SQL
  rapides ("tous les HIGH d'une école"). On garde l'enum séparé de
  ``IncidentSeverity`` / ``IncidentStatus`` (semantique différente).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.predictions.enums import DropoutRiskLevel
from app.shared.base import Base, CreatedAtMixin, cuid_pk

if TYPE_CHECKING:
    from app.modules.academics.models import SchoolYear
    from app.modules.census.models import Student


class DropoutPrediction(Base, CreatedAtMixin):
    """Score de risque d'abandon scolaire calculé pour un élève à un instant T.

    Un nouveau row est inséré à chaque exécution du pipeline (à la demande,
    en batch école ou via la tâche mensuelle). Le ``featuresSnapshot`` JSONB
    conserve les valeurs exactes utilisées pour produire le score — utile
    pour debug et pour répondre à un parent qui demande "pourquoi mon enfant
    est-il classé à risque ?".
    """

    __tablename__ = "DropoutPrediction"
    __table_args__ = (
        Index(
            "ix_DropoutPrediction_studentId_computedAt",
            "studentId", "computedAt",
        ),
        Index("ix_DropoutPrediction_riskLevel", "riskLevel"),
    )

    id: Mapped[str] = cuid_pk()
    studentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=False,
    )
    schoolYearId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=True,
    )
    computedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    riskLevel: Mapped[DropoutRiskLevel] = mapped_column(
        Enum(DropoutRiskLevel, name="DropoutRiskLevel", native_enum=True),
        default=DropoutRiskLevel.LOW, nullable=False,
        server_default="LOW",
    )
    featuresSnapshot: Mapped[Any] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False, default=dict,
    )
    modelVersion: Mapped[str] = mapped_column(String(20), nullable=False)

    student: Mapped[Student] = relationship(lazy="raise")
    schoolYear: Mapped[SchoolYear | None] = relationship(lazy="raise")


class DropoutModelMetadata(Base, CreatedAtMixin):
    """Registry minimaliste des modèles entraînés.

    Quand on entraîne un nouveau modèle, on insère un row ici avec sa
    version (ex. ``v1-2026-05``), les métriques (accuracy, ROC AUC, etc.)
    et le chemin vers l'artefact joblib (local ``/tmp`` en MVP, S3 plus
    tard). Le service de prédiction lit la version la plus récente au
    démarrage et la garde en cache process-level.
    """

    __tablename__ = "DropoutModelMetadata"
    __table_args__ = (
        UniqueConstraint("version", name="uq_DropoutModelMetadata_version"),
        Index("ix_DropoutModelMetadata_trainedAt", "trainedAt"),
    )

    id: Mapped[str] = cuid_pk()
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    trainedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    metrics: Mapped[Any] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False, default=dict,
    )
    artifactPath: Mapped[str] = mapped_column(String(500), nullable=False)
