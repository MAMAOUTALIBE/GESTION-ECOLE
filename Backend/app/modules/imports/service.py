"""Imports service — orchestrates parse → validate → queue commit.

Preview is sync (bounded to 10k rows). Commit queues a Celery task that
processes the rows in chunks and writes AuditLog entries for traceability.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.modules.auth.models import User
from app.modules.imports.parsers import ImportKind, parse_workbook
from app.modules.imports.schemas import (
    ImportCommitRequest,
    ImportCommitResponse,
    ImportPreviewResponse,
    ImportPreviewRow,
    ImportSummary,
)
from app.modules.imports.templates import render_template
from app.modules.workflow.models import AuditLog


class ImportsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ==================================================================
    # PREVIEW (sync)
    # ==================================================================
    async def preview(
        self, user: User, kind: ImportKind, content: bytes
    ) -> ImportPreviewResponse:
        if len(content) > 10 * 1024 * 1024:  # 10 MiB hard cap
            raise ConflictError(
                detail="Fichier trop volumineux (10 Mo max). Utiliser plusieurs lots."
            )
        result = parse_workbook(content, kind)

        self.session.add(
            AuditLog(
                actorId=user.id,
                action="IMPORT_PREVIEW",
                entity="Import",
                entityId=None,
                metadata_={
                    "kind": kind,
                    "total": result.summary.total,
                    "valid": result.summary.valid,
                    "invalid": result.summary.invalid,
                },
            )
        )
        await self.session.flush()

        return ImportPreviewResponse(
            kind=kind,
            headers=result.headers,
            summary=ImportSummary(**result.summary.__dict__),
            rows=[
                ImportPreviewRow(
                    rowIndex=r.rowIndex, valid=r.valid, errors=r.errors, data=r.data
                )
                for r in result.rows
            ],
        )

    # ==================================================================
    # COMMIT (async via Celery)
    # ==================================================================
    async def commit(
        self, user: User, kind: ImportKind, dto: ImportCommitRequest
    ) -> ImportCommitResponse:
        eligible = [r for r in dto.rows if r.valid] if dto.skipInvalid else dto.rows
        skipped = len(dto.rows) - len(eligible)
        if not eligible:
            raise ConflictError(
                detail="Aucune ligne valide à importer (toutes invalides ou liste vide)."
            )

        payload = [r.data for r in eligible]
        self.session.add(
            AuditLog(
                actorId=user.id,
                action="IMPORT_COMMIT",
                entity="Import",
                entityId=None,
                metadata_={
                    "kind": kind,
                    "queued": len(payload),
                    "skipped": skipped,
                },
            )
        )
        await self.session.flush()

        from app.workers.import_tasks import import_rows

        task = import_rows.delay(kind, payload, requested_by=user.id)
        return ImportCommitResponse(
            queued=len(payload), skipped=skipped, taskId=task.id
        )

    # ==================================================================
    # TEMPLATES
    # ==================================================================
    @staticmethod
    def template(kind: ImportKind) -> bytes:
        return render_template(kind)
