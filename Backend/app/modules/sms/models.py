"""Module 14 — SMS / USSD gateway : modèles SQLAlchemy.

Deux tables append-only (sauf ``status`` qui change OUTBOUND→DELIVERED) :

* :class:`SmsMessage` — chaque message SMS envoyé ou reçu. ``direction``
  distingue OUTBOUND (notifications parents, alertes) vs INBOUND (réponses
  / messages entrants — rare pour le MVP, prévu pour évolutions futures).
  ``providerId`` est l'identifiant retourné par Twilio/Orange (utile pour
  rapprocher les callbacks de delivery report).
* :class:`UssdSession` — état d'une session USSD interactive
  (``*999*CODE#``). On persiste le ``sessionId`` opérateur, le numéro,
  le service code, le dernier input et l'étape courante (state machine
  côté ``ussd.py``).

Pourquoi pas d'enum natif Postgres sur ``direction`` / ``status`` ?
On utilise les enums Postgres natifs pour rester cohérent avec le reste
du codebase (Module 11 DiplomaStatus, Module 9 AnomalyStatus, etc.). Le
type s'appelle ``SmsDirection`` / ``SmsStatus`` côté base — voir la
migration 0019.

Indexes
-------
* ``SmsMessage.to_`` — recherche par destinataire (debug / support).
* ``SmsMessage.status`` — KPIs (taux de delivery, throughput).
* ``SmsMessage.createdAt`` — fenêtres temporelles.
* ``UssdSession.phoneNumber`` — rate-limit par numéro + historique.
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

from app.modules.sms.enums import SmsDirection, SmsStatus
from app.shared.base import Base, CreatedAtMixin, TimestampMixin, cuid_pk


class SmsMessage(Base, CreatedAtMixin):
    """Un message SMS individuel (OUTBOUND par défaut, INBOUND si reçu)."""

    __tablename__ = "SmsMessage"
    __table_args__ = (
        Index("ix_SmsMessage_to", "to"),
        Index("ix_SmsMessage_status", "status"),
        Index("ix_SmsMessage_createdAt", "createdAt"),
        Index("ix_SmsMessage_providerId", "providerId"),
    )

    id: Mapped[str] = cuid_pk()
    direction: Mapped[SmsDirection] = mapped_column(
        Enum(
            SmsDirection, name="SmsDirection", native_enum=True,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
        default=SmsDirection.OUTBOUND,
        server_default="OUTBOUND",
    )
    # ``to`` est un mot reservé Python en certains contextes — on suffixe d'un
    # underscore côté ORM mais on garde la colonne SQL en ``to``.
    to_: Mapped[str] = mapped_column("to", String(20), nullable=False)
    from_: Mapped[str | None] = mapped_column("from", String(20), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[SmsStatus] = mapped_column(
        Enum(
            SmsStatus, name="SmsStatus", native_enum=True,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
        default=SmsStatus.PENDING,
        server_default="PENDING",
    )
    providerId: Mapped[str | None] = mapped_column(String(80), nullable=True)
    errorMessage: Mapped[str | None] = mapped_column(Text, nullable=True)
    actorId: Mapped[str | None] = mapped_column(String(30), nullable=True)
    deliveredAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class UssdSession(Base, TimestampMixin):
    """État d'une session USSD persistée pour reprendre les étapes."""

    __tablename__ = "UssdSession"
    __table_args__ = (
        UniqueConstraint("sessionId", name="uq_UssdSession_sessionId"),
        Index("ix_UssdSession_phoneNumber", "phoneNumber"),
        Index("ix_UssdSession_createdAt", "createdAt"),
    )

    id: Mapped[str] = cuid_pk()
    sessionId: Mapped[str] = mapped_column(String(80), nullable=False)
    phoneNumber: Mapped[str] = mapped_column(String(20), nullable=False)
    serviceCode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    lastInput: Mapped[str | None] = mapped_column(Text, nullable=True)
    currentStep: Mapped[str] = mapped_column(
        String(40), nullable=False, default="MENU", server_default="MENU",
    )
    completedAt: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
