"""Module 5C — Modèle SQLAlchemy de l'audit PII.

Table unique : ``PiiAccessLog`` — append-only, sans ``updatedAt``.
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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.pii_audit.enums import PiiAccessType, PiiEntityType
from app.shared.base import Base, cuid_pk


class PiiAccessLog(Base):
    """Trace immuable d'un accès en lecture à une entité PII.

    Notes :

    * ``userId`` est NULLABLE + ``ON DELETE SET NULL`` : la suppression
      d'un user (RGPD Art. 17) ne doit pas casser l'audit trail.
    * ``entityId`` est un VARCHAR(30) suffisant pour les cuid Prisma.
      Pour les LIST agrégés on stocke ``"*"`` et on précise le compte
      via ``metadataJson``.
    * ``userAgent`` est borné à 512 octets côté schéma — la couche
      service tronque + assainit pour neutraliser les caractères de
      contrôle (compat Module 1.1 H-3).
    """

    __tablename__ = "PiiAccessLog"
    __table_args__ = (
        Index(
            "ix_PiiAccessLog_userId_accessedAt",
            "userId",
            "accessedAt",
        ),
        Index(
            "ix_PiiAccessLog_entity_accessedAt",
            "entityType",
            "entityId",
            "accessedAt",
        ),
        Index(
            "ix_PiiAccessLog_accessedAt",
            "accessedAt",
        ),
    )

    id: Mapped[str] = cuid_pk()
    userId: Mapped[str | None] = mapped_column(
        String(30),
        ForeignKey("User.id", ondelete="SET NULL"),
        nullable=True,
    )
    userRole: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entityType: Mapped[PiiEntityType] = mapped_column(
        Enum(PiiEntityType, name="PiiEntityType", native_enum=True),
        nullable=False,
    )
    entityId: Mapped[str] = mapped_column(String(30), nullable=False)
    accessType: Mapped[PiiAccessType] = mapped_column(
        Enum(PiiAccessType, name="PiiAccessType", native_enum=True),
        nullable=False,
    )
    endpoint: Mapped[str] = mapped_column(String(200), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    userAgent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    requestId: Mapped[str | None] = mapped_column(String(60), nullable=True)
    metadataJson: Mapped[Any | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
    )
    accessedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


__all__ = ["PiiAccessLog"]
