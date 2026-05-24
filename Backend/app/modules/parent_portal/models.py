"""Module 18 — Modèles SQLAlchemy : ParentSession + WhatsAppMessage.

Append-only (sauf ``ParentSession.lastActivityAt`` qui peut être bumpé).

* :class:`ParentSession` — une session parent identifiée par
  ``phoneNumberHash`` (SHA-256 hex, jamais le numéro en clair). ``channel``
  segmente WHATSAPP / USSD / WEB. ``expiresAt`` permet d'expirer la
  session (30 min d'inactivité par défaut).
* :class:`WhatsAppMessage` — journal des messages WhatsApp INBOUND et
  OUTBOUND. ``messageId`` UNIQUE pour idempotency / anti-replay.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.parent_portal.enums import (
    ParentChannel,
    WhatsAppDirection,
    WhatsAppStatus,
)
from app.shared.base import Base, cuid_pk


class ParentSession(Base):
    """Session parent sur un canal donné."""

    __tablename__ = "ParentSession"
    __table_args__ = (
        Index("ix_ParentSession_phoneNumberHash", "phoneNumberHash"),
        Index("ix_ParentSession_channel", "channel"),
        Index("ix_ParentSession_expiresAt", "expiresAt"),
    )

    id: Mapped[str] = cuid_pk()
    phoneNumberHash: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[ParentChannel] = mapped_column(
        Enum(
            ParentChannel, name="ParentChannel", native_enum=True,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    startedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    lastActivityAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    expiresAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )


class WhatsAppMessage(Base):
    """Un message WhatsApp INBOUND ou OUTBOUND, append-only."""

    __tablename__ = "WhatsAppMessage"
    __table_args__ = (
        UniqueConstraint("messageId", name="uq_WhatsAppMessage_messageId"),
        Index("ix_WhatsAppMessage_phoneNumber", "phoneNumber"),
        Index("ix_WhatsAppMessage_receivedAt", "receivedAt"),
    )

    id: Mapped[str] = cuid_pk()
    direction: Mapped[WhatsAppDirection] = mapped_column(
        Enum(
            WhatsAppDirection, name="WhatsAppDirection", native_enum=True,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    phoneNumber: Mapped[str] = mapped_column(String(20), nullable=False)
    messageId: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[WhatsAppStatus] = mapped_column(
        Enum(
            WhatsAppStatus, name="WhatsAppStatus", native_enum=True,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
        default=WhatsAppStatus.RECEIVED,
        server_default="RECEIVED",
    )
    receivedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    processedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
