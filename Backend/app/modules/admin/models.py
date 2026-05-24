"""Module 15 — Admin / Settings plateforme.

Trois entités :

* ``PlatformSetting`` — paramètres clé/valeur typés persistés. La valeur
  applicative vit dans ``valueJson`` (JSONB) ; ``type`` permet de valider
  le typage à l'écriture côté service.
* ``FeatureFlag`` — drapeaux booléens + rollout 0..100 % (canary).
* ``SettingChangeLog`` — audit append-only (qui, quand, avant/après).

Aucune relation FK déclarée vers ``User`` ici (les colonnes restent des
String 30) : ça évite les imports circulaires côté SQLAlchemy et reste
cohérent avec la convention du projet (cf. SmsMessage.actorId, etc.).
"""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.admin.enums import SettingChangeKind, SettingType
from app.shared.base import Base, cuid_pk


class PlatformSetting(Base):
    __tablename__ = "PlatformSetting"

    id: Mapped[str] = cuid_pk()
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SettingType.STRING.value,
    )
    valueJson: Mapped[Any] = mapped_column(JSONB, nullable=False, default=None)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updatedById: Mapped[str | None] = mapped_column(String(30), nullable=True)
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class FeatureFlag(Base):
    __tablename__ = "FeatureFlag"
    __table_args__ = (
        # La convention ck_%(table_name)s_%(constraint_name)s ajoute le
        # préfixe -> nom final = ``ck_FeatureFlag_rollout_range``.
        CheckConstraint(
            '"rolloutPercentage" >= 0 AND "rolloutPercentage" <= 100',
            name="rollout_range",
        ),
    )

    id: Mapped[str] = cuid_pk()
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rolloutPercentage: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SettingChangeLog(Base):
    __tablename__ = "SettingChangeLog"

    id: Mapped[str] = cuid_pk()
    key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    oldValue: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    newValue: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    changedById: Mapped[str | None] = mapped_column(String(30), nullable=True)
    changedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )


__all__ = [
    "FeatureFlag",
    "PlatformSetting",
    "SettingChangeKind",
    "SettingChangeLog",
    "SettingType",
]
