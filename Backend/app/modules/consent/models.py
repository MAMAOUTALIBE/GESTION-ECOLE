"""Module 5B — Modèle SQLAlchemy du consentement utilisateur."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import Base, TimestampMixin, cuid_pk

if TYPE_CHECKING:
    from app.modules.auth.models import User


class UserConsent(Base, TimestampMixin):
    """Trace l'acceptation par un utilisateur d'une version du contrat.

    Une ligne UNIQUE par utilisateur — l'upsert remplace toujours
    la précédente. ``acceptedAt`` + ``consentVersion`` suffisent à
    prouver la dernière acceptation. ``ip`` + ``userAgent`` permettent
    de défendre la traçabilité en cas de contestation.
    """

    __tablename__ = "UserConsent"
    __table_args__ = (
        Index("ix_UserConsent_userId", "userId"),
        Index("ix_UserConsent_consentVersion", "consentVersion"),
    )

    id: Mapped[str] = cuid_pk()
    userId: Mapped[str] = mapped_column(
        String(30),
        ForeignKey("User.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    consentVersion: Mapped[str] = mapped_column(String(20), nullable=False)
    acceptedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    userAgent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    user: Mapped["User | None"] = relationship(
        foreign_keys=[userId], lazy="raise"
    )


__all__ = ["UserConsent"]
