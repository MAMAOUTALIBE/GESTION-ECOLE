from datetime import datetime
from typing import Any

from cuid2 import Cuid
from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_cuid_generator = Cuid(length=25)


def generate_cuid() -> str:
    """Generate a Prisma-compatible cuid string (25 chars)."""
    return _cuid_generator.generate()


# Naming convention so Alembic generates stable, predictable constraint names
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map: dict[Any, Any] = {}


def cuid_pk() -> Mapped[str]:
    """Primary key column matching Prisma `cuid()` defaults."""
    return mapped_column(String(30), primary_key=True, default=generate_cuid)


class TimestampMixin:
    """Adds createdAt + updatedAt columns matching Prisma defaults."""

    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updatedAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CreatedAtMixin:
    """Adds only createdAt (for append-only tables)."""

    createdAt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
