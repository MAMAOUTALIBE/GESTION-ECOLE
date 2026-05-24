"""Notifications module ORM models.

Currently a single table: :class:`NotificationTemplate` — the i18n template
catalogue used by :func:`app.modules.notifications.i18n.render_template`.

A template is uniquely identified by ``(key, language, channel)``:

* ``key`` is a logical event name (e.g. ``validation.created``).
* ``language`` is one of the supported codes (``fr``, ``ff``, ``sus``,
  ``man``) — see :data:`app.modules.notifications.i18n.SUPPORTED_LANGUAGES`.
* ``channel`` is the lowercase canonical channel name (``sms``, ``email``,
  ``in_app``, ``whatsapp``, ``push``).

The ``body`` column stores a mustache-style template with ``{{varName}}``
placeholders. ``variables`` is a JSONB array of variable names that the
renderer expects to receive — useful for the admin UI to validate the
payload before sending.
"""
from __future__ import annotations

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, TimestampMixin, cuid_pk


class NotificationTemplate(Base, TimestampMixin):
    """Multilingual notification template keyed by (key, language, channel)."""

    __tablename__ = "NotificationTemplate"
    __table_args__ = (
        UniqueConstraint(
            "key", "language", "channel",
            name="uq_NotificationTemplate_key_language_channel",
        ),
        Index("ix_NotificationTemplate_key", "key"),
    )

    id: Mapped[str] = cuid_pk()
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(200), nullable=True)
    body: Mapped[str] = mapped_column(String, nullable=False)
    variables: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
