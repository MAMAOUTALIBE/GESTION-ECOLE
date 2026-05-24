"""Module 11 — Diplômes signés numériquement : modèle SQLAlchemy.

Une seule table ``Diploma`` (append-only sauf changement de ``status``).

Conventions
-----------
* ``serial`` est l'identifiant publiquement vérifiable. Format
  ``{TYPE}-{YEAR}-{8HEX}`` (ex. ``CEPE-2026-3F2A91BC``). Unique.
* ``payloadSha256`` = SHA-256 hex du payload canonicalisé (sorted keys, no
  whitespace) qui a été signé. Permet une vérification "offline" : on
  recompute le hash depuis le payload retourné par l'API publique, et on
  vérifie qu'il matche ce stocké en DB.
* ``signature`` = signature Ed25519 base64 du SHA-256 (binaire 32 bytes
  → 64 chars base64). On stocke base64 plutôt que binaire pour faciliter
  le copier-coller et la migration JSON.
* ``publicKeyFingerprint`` = SHA-256[0:16] de la clé publique au format
  raw bytes, hex. Trace quelle clé a signé — utile pour les rotations
  futures (Module 11.x).
* ``status`` reste ``DRAFT`` jusqu'à ``issue_diploma`` qui le passe à
  ``ISSUED``. ``REVOKED`` est une action explicite côté admin national.
* ``pdfS3Key`` est nullable pour le MVP — la génération PDF est optionnelle.
* Indexes : ``serial`` unique (recherche publique), ``studentId``
  (historique d'un élève), ``status`` (KPIs nationaux), composite
  ``(schoolId, status)`` (listing par école).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.modules.diplomas.enums import DiplomaStatus, DiplomaType
from app.shared.base import Base, TimestampMixin, cuid_pk

if TYPE_CHECKING:
    from app.modules.academics.models import SchoolYear
    from app.modules.census.models import Student
    from app.modules.schools.models import School


class Diploma(Base, TimestampMixin):
    """Un diplôme signé numériquement (Ed25519).

    Vérifiable publiquement (sans auth) via ``/api/diplomas/verify/{serial}``.
    La signature est calculée sur un payload JSON canonicalisé contenant
    les informations académiques essentielles (élève, école, score, etc.).
    """

    __tablename__ = "Diploma"
    __table_args__ = (
        UniqueConstraint("serial", name="uq_Diploma_serial"),
        Index("ix_Diploma_studentId", "studentId"),
        Index("ix_Diploma_status", "status"),
        Index("ix_Diploma_schoolId_status", "schoolId", "status"),
        Index("ix_Diploma_diplomaType_academicYearId",
              "diplomaType", "academicYearId"),
    )

    id: Mapped[str] = cuid_pk()
    serial: Mapped[str] = mapped_column(String(40), nullable=False)

    studentId: Mapped[str] = mapped_column(
        String(30), ForeignKey("Student.id"), nullable=False,
    )
    diplomaType: Mapped[DiplomaType] = mapped_column(
        Enum(
            DiplomaType, name="DiplomaType", native_enum=True,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    academicYearId: Mapped[str | None] = mapped_column(
        String(30), ForeignKey("SchoolYear.id"), nullable=True,
    )
    schoolId: Mapped[str] = mapped_column(
        String(30), ForeignKey("School.id"), nullable=False,
    )

    # Identification physique du centre d'examen — peut différer de l'école
    # de scolarité (regroupement régional pour le BEPC).
    examCenter: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Score sur 20 (ou pourcentage selon le diplôme). Peut être null pour
    # un diplôme purement qualitatif (rare). Mention dérivée (Passable, Bien…).
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    mention: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Dates métier
    issuedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    signedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Cryptographie — voir crypto.py pour les détails.
    payloadSha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    publicKeyFingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )

    # PDF (optionnel pour MVP — la signature reste valable sans).
    pdfS3Key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[DiplomaStatus] = mapped_column(
        Enum(
            DiplomaStatus, name="DiplomaStatus", native_enum=True,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
        default=DiplomaStatus.DRAFT,
        server_default="DRAFT",
    )
    revokedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    revokedReason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relations (lazy=raise pour rester explicite — un accès relationnel
    # implicite déclenche une erreur, on doit éager-load via selectinload).
    student: Mapped["Student"] = relationship(lazy="raise")
    school: Mapped["School"] = relationship(lazy="raise")
    academicYear: Mapped["SchoolYear | None"] = relationship(lazy="raise")
