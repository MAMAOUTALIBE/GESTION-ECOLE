"""Module 12 — OpendataService : orchestre catalogue, données, audit.

Le service est volontairement minimaliste : il assemble le registry
statique des datasets (``datasets.py``) avec les métadonnées DB
(``OpendataDataset``) et l'audit anonyme (``OpendataDownload``).

Politique de rafraîchissement
-----------------------------
* Le catalogue (``list_datasets``) est calculé à partir de l'union de
  :data:`DATASETS` (source de vérité) et de la table ``OpendataDataset``
  (uniquement pour la métadonnée ``lastRefreshedAt`` / ``recordCount``).
* :meth:`refresh_dataset_metadata` peut être appelé par un job planifié
  (Celery beat) ou manuellement par un admin. Pour le MVP, on garde un
  ``upsert`` simple : si la ligne n'existe pas on l'insère, sinon on
  met à jour les compteurs.

Anti-leak
---------
:meth:`get_dataset_data` vérifie via :func:`is_anonymous` (côté tests)
qu'aucun record ne contient un champ ressemblant à un PII. Le service
fait confiance aux fonctions ``fetch_*`` du registry, mais en cas de
régression future le test ``test_anonymization_no_pii_in_response``
détectera la fuite.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.opendata.datasets import DATASETS, get_dataset_spec
from app.modules.opendata.models import OpendataDataset, OpendataDownload
from app.modules.opendata.schemas import DatasetMetadata, OpendataStats
from app.shared.base import generate_cuid


class OpendataService:
    """Service unique pour le catalogue, les données et l'audit."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # =====================================================================
    # Catalogue
    # =====================================================================
    async def list_datasets(self) -> list[DatasetMetadata]:
        """Renvoie le catalogue complet : 6 datasets statiques + meta DB.

        On utilise le registry :data:`DATASETS` comme source de vérité
        (titre / description / schéma) et on enrichit chaque entrée avec
        la ligne ``OpendataDataset`` correspondante si elle existe en DB.
        """
        # Charger toutes les rows DB d'un seul coup pour éviter N+1.
        rows = (await self.session.execute(
            select(OpendataDataset)
        )).scalars().all()
        by_key: dict[str, OpendataDataset] = {r.key: r for r in rows}

        items: list[DatasetMetadata] = []
        for spec in DATASETS:
            row = by_key.get(spec.key)
            items.append(
                DatasetMetadata(
                    key=spec.key,
                    title=spec.title,
                    description=spec.description,
                    license=spec.license,
                    refreshFrequency=spec.refresh_frequency,
                    schemaJsonb=spec.schema,
                    lastRefreshedAt=row.lastRefreshedAt if row else None,
                    recordCount=row.recordCount if row else None,
                    sizeKb=row.sizeKb if row else None,
                )
            )
        return items

    async def get_dataset_metadata(self, key: str) -> DatasetMetadata | None:
        """Détails d'un dataset par key (None si inconnu)."""
        spec = get_dataset_spec(key)
        if spec is None:
            return None
        row = (await self.session.execute(
            select(OpendataDataset).where(OpendataDataset.key == key)
        )).scalar_one_or_none()
        return DatasetMetadata(
            key=spec.key,
            title=spec.title,
            description=spec.description,
            license=spec.license,
            refreshFrequency=spec.refresh_frequency,
            schemaJsonb=spec.schema,
            lastRefreshedAt=row.lastRefreshedAt if row else None,
            recordCount=row.recordCount if row else None,
            sizeKb=row.sizeKb if row else None,
        )

    # =====================================================================
    # Données
    # =====================================================================
    async def get_dataset_data(
        self, key: str, format: str = "json",
    ) -> tuple[bytes, str] | None:
        """Renvoie ``(payload_bytes, content_type)`` pour un dataset.

        ``None`` si la key est inconnue (router → 404).

        Formats supportés
        -----------------
        * ``json`` (défaut) — UTF-8, compact (sans indentation pour
          réduire la bande passante).
        * ``csv`` — RFC 4180, header sur la première ligne, encodage
          UTF-8 avec BOM (Excel compatibility).
        """
        spec = get_dataset_spec(key)
        if spec is None:
            return None

        records = await spec.fetch(self.session)

        if format == "csv":
            return _records_to_csv(records), "text/csv; charset=utf-8"
        # Default JSON
        payload = json.dumps(
            records, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        return payload, "application/json"

    # =====================================================================
    # Audit
    # =====================================================================
    async def log_download(
        self, key: str, ip_hash: str, format: str,
    ) -> OpendataDownload:
        """Persist append-only une entrée d'audit (aucun PII).

        Le service ne commit pas — le router laisse la session FastAPI
        gérer le cycle de vie de la transaction.
        """
        entry = OpendataDownload(
            id=generate_cuid(),
            datasetKey=key,
            ipHash=ip_hash,
            format=format,
            downloadedAt=datetime.now(UTC),
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    # =====================================================================
    # Refresh des métadonnées (recordCount, lastRefreshedAt)
    # =====================================================================
    async def refresh_dataset_metadata(
        self, key: str,
    ) -> OpendataDataset | None:
        """Met à jour ``recordCount`` + ``lastRefreshedAt`` (upsert).

        Renvoie la ligne mise à jour, ``None`` si la key n'est pas dans
        le registry. Peut être appelé par un job planifié.
        """
        spec = get_dataset_spec(key)
        if spec is None:
            return None
        records = await spec.fetch(self.session)
        count = len(records)
        # Approximation de la taille : 1 record JSON moyenne ≈ 150 bytes.
        size_kb = max(1, int(count * 150 / 1024))

        existing = (await self.session.execute(
            select(OpendataDataset).where(OpendataDataset.key == key)
        )).scalar_one_or_none()
        now = datetime.now(UTC)
        if existing is None:
            row = OpendataDataset(
                id=generate_cuid(),
                key=spec.key,
                title=spec.title,
                description=spec.description,
                license=spec.license,
                schemaJsonb=spec.schema,
                refreshFrequency=spec.refresh_frequency,
                lastRefreshedAt=now,
                recordCount=count,
                sizeKb=size_kb,
            )
            self.session.add(row)
            await self.session.flush()
            return row
        existing.lastRefreshedAt = now
        existing.recordCount = count
        existing.sizeKb = size_kb
        await self.session.flush()
        return existing

    # =====================================================================
    # Stats (anonymes)
    # =====================================================================
    async def get_stats(self) -> OpendataStats:
        """Renvoie les compteurs agrégés des downloads (anonymes)."""
        total = (await self.session.execute(
            select(func.count()).select_from(OpendataDownload)
        )).scalar_one()

        by_dataset_rows = (await self.session.execute(
            select(OpendataDownload.datasetKey, func.count())
            .group_by(OpendataDownload.datasetKey)
        )).all()
        by_format_rows = (await self.session.execute(
            select(OpendataDownload.format, func.count())
            .group_by(OpendataDownload.format)
        )).all()

        return OpendataStats(
            totalDownloads=int(total),
            downloadsByDataset={
                key: int(count) for key, count in by_dataset_rows
            },
            downloadsByFormat={
                fmt: int(count) for fmt, count in by_format_rows
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _records_to_csv(records: list[dict[str, Any]]) -> bytes:
    """Sérialise une liste de dict homogènes en CSV UTF-8 (avec BOM Excel).

    * Header = clés du premier record (les ``fetch_*`` du registry
      garantissent que tous les records ont la même forme).
    * Si la liste est vide on renvoie juste le BOM + un newline pour
      que les outils CSV ne plantent pas sur une réponse vide.
    """
    buf = io.StringIO()
    if not records:
        # BOM UTF-8 seul pour signaler l'encodage à Excel.
        return b"\xef\xbb\xbf"
    fieldnames = list(records[0].keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in records:
        writer.writerow(r)
    # BOM UTF-8 en tête pour qu'Excel détecte correctement l'encodage.
    return ("﻿" + buf.getvalue()).encode("utf-8")


__all__ = ["OpendataService"]
