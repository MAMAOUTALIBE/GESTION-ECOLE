"""Helpers pour gérer les partitions mensuelles de ``AttendanceRecord``.

PostgreSQL 16 supporte le partitionnement déclaratif natif par RANGE. Pour
``AttendanceRecord``, on partitionne par ``scannedAt`` :
* une partition par MOIS (sweet spot ~50M lignes/partition à 3M élèves) ;
* une partition ``AttendanceRecord_default`` catch-all pour les dates hors
  range — fail-safe en cas de scan avec une date corrompue.

Indexes : SqlAlchemy crée les indexes sur la table parente partitionnée et
PostgreSQL les propage automatiquement à chaque partition existante et
future. Aucun travail manuel n'est requis par partition.

Convention de nommage : ``AttendanceRecord_YYYY_MM`` (zero-padded mois).
On garde le casing CamelCase identique au nom de la table parente pour
être cohérent avec le schéma Prisma legacy (qui n'utilise pas snake_case).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Format strict : YYYY entre 1900 et 2999, MM entre 01 et 12.
# Évite tout risque d'injection de nom de partition arbitraire.
_PARTITION_NAME_RE = re.compile(r"^AttendanceRecord_(\d{4})_(\d{2})$")


def partition_name(year: int, month: int) -> str:
    """Nom canonique d'une partition mensuelle."""
    if not (1900 <= year <= 2999):
        raise ValueError(f"year hors range: {year}")
    if not (1 <= month <= 12):
        raise ValueError(f"month hors range: {month}")
    return f"AttendanceRecord_{year:04d}_{month:02d}"


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    """Renvoie (start_of_month, start_of_next_month) — borne exclusive à droite."""
    if month == 12:
        return date(year, 12, 1), date(year + 1, 1, 1)
    return date(year, month, 1), date(year, month + 1, 1)


def make_partition_sql(year: int, month: int) -> str:
    """Génère le ``CREATE TABLE ... PARTITION OF`` pour un mois donné.

    Idempotent (``IF NOT EXISTS``) afin de pouvoir être ré-exécuté en cron
    sans risque. Les indexes héritent de la table parente automatiquement.
    """
    name = partition_name(year, month)
    start, end = _month_bounds(year, month)
    return (
        f'CREATE TABLE IF NOT EXISTS "{name}" '
        f'PARTITION OF "AttendanceRecord" '
        f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}');"
    )


def _next_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


async def ensure_future_partitions(
    session: AsyncSession,
    months_ahead: int = 3,
    *,
    today: date | None = None,
) -> list[str]:
    """Crée les partitions manquantes pour ``months_ahead`` mois en avance.

    Inclut systématiquement le mois courant : on garantit qu'une insertion
    "aujourd'hui" trouvera toujours une partition cible (sans tomber dans
    la partition ``_default`` qui est plus lente et moins indexable).

    Retourne la liste des partitions effectivement créées (utile pour log
    et pour les tests d'idempotence).
    """
    if months_ahead < 0:
        raise ValueError("months_ahead doit être >= 0")
    anchor = (today or date.today()).replace(day=1)

    targets: list[tuple[int, int]] = []
    cursor = anchor
    # +1 pour inclure le mois courant ET months_ahead mois futurs
    for _ in range(months_ahead + 1):
        targets.append((cursor.year, cursor.month))
        cursor = _next_month(cursor)

    # Liste existante (un SELECT, pas N requêtes).
    existing = {row["name"] for row in await _raw_list_partitions(session)}
    created: list[str] = []
    for year, month in targets:
        name = partition_name(year, month)
        if name in existing:
            continue
        await session.execute(text(make_partition_sql(year, month)))
        created.append(name)
    return created


async def _raw_list_partitions(session: AsyncSession) -> list[dict[str, Any]]:
    """Liste brute des partitions de ``AttendanceRecord`` (sans tailles).

    Utilise ``pg_inherits`` pour énumérer les enfants de la table parente.
    """
    rows = await session.execute(
        text(
            """
            SELECT c.relname AS name
            FROM pg_inherits i
            JOIN pg_class p ON p.oid = i.inhparent
            JOIN pg_class c ON c.oid = i.inhrelid
            WHERE p.relname = 'AttendanceRecord'
            ORDER BY c.relname
            """
        )
    )
    return [{"name": r[0]} for r in rows.fetchall()]


async def list_partitions(session: AsyncSession) -> list[dict[str, Any]]:
    """Liste des partitions avec leurs bornes + nombre de lignes + taille.

    Renvoie ``[{name, start, end, rowCount, sizeMb}, ...]`` triées par
    nom (ce qui équivaut à un tri chronologique grâce au format YYYY_MM).
    """
    base = await _raw_list_partitions(session)
    results: list[dict[str, Any]] = []
    for row in base:
        name = row["name"]
        match = _PARTITION_NAME_RE.match(name)
        if match is None:
            # partition_default ou autre — on l'expose quand même
            start_d: date | None = None
            end_d: date | None = None
        else:
            year, month = int(match.group(1)), int(match.group(2))
            start_d, end_d = _month_bounds(year, month)

        count_row = await session.execute(
            text(f'SELECT COUNT(*) FROM "{name}"')
        )
        size_row = await session.execute(
            text(
                "SELECT pg_total_relation_size(:t)::bigint AS size_bytes"
            ),
            {"t": f'"{name}"'},
        )
        size_bytes = int(size_row.scalar() or 0)
        results.append(
            {
                "name": name,
                "start": start_d or date(1900, 1, 1),
                "end": end_d or date(2999, 12, 31),
                "rowCount": int(count_row.scalar() or 0),
                "sizeMb": round(size_bytes / (1024 * 1024), 3),
            }
        )
    return results


async def get_partition_size_mb(session: AsyncSession, name: str) -> float:
    """Taille totale (data + indexes + toast) d'une partition en Mo."""
    if not _PARTITION_NAME_RE.match(name) and name != "AttendanceRecord_default":
        raise ValueError(f"Nom de partition invalide: {name!r}")
    row = await session.execute(
        text("SELECT pg_total_relation_size(:t)::bigint"),
        {"t": f'"{name}"'},
    )
    size_bytes = int(row.scalar() or 0)
    return round(size_bytes / (1024 * 1024), 3)


__all__ = [
    "ensure_future_partitions",
    "get_partition_size_mb",
    "list_partitions",
    "make_partition_sql",
    "partition_name",
]
