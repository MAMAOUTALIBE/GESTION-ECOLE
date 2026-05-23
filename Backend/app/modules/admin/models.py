"""Admin / Configuration plateforme — Phase 13bis.

Une seule table : PlatformSetting (clé/valeur typée par catégorie).
Persiste les seuils, canaux préférés et règles éditables sans déploiement.
"""
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import Base, TimestampMixin, cuid_pk


class PlatformSetting(Base, TimestampMixin):
    __tablename__ = "PlatformSetting"

    id: Mapped[str] = cuid_pk()
    key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False)  # JSON-encoded
    category: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    valueType: Mapped[str] = mapped_column(
        String, default="string", nullable=False,
    )  # string | number | boolean | json
    updatedById: Mapped[str | None] = mapped_column(String(30), nullable=True)
