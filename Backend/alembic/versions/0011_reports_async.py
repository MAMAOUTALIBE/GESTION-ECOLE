"""module 4 — reports/bulletins : génération PDF asynchrone via Celery

Revision ID: 0011_reports_async
Revises: 0010_attendance_partition
Create Date: 2026-05-24

Pourquoi ?
----------
La génération synchrone d'un bulletin PDF (WeasyPrint) bloque l'event loop
FastAPI pendant plusieurs centaines de millisecondes par requête. À l'échelle
d'une école (200-400 bulletins par trimestre), ça suffit à saturer les workers.

On passe la génération en asynchrone via Celery :
1. Le handler HTTP enregistre l'intention (``pdfStatus = PENDING``) puis
   enqueue un task.
2. Le worker lit le ReportCard, rend le PDF, calcule SHA-256 et l'upload S3.
3. Le frontend poll ``GET /api/reports/<id>/status`` jusqu'à ``DONE`` puis
   redirige vers l'URL S3 presignée.

Colonnes ajoutées à ``ReportCard``
----------------------------------
* ``pdfStatus``      — enum (PENDING|PROCESSING|DONE|FAILED) — défaut PENDING.
  Permet une recherche rapide des bulletins coincés (index dédié).
* ``pdfS3Key``       — clé S3 ``bulletins/<schoolId>/<periodId>/<studentId>.pdf``.
* ``pdfSha256``      — hash hex (64 chars). Utilisé pour la vérification UI
  ("aucune modification du PDF depuis génération") et l'idempotence.
* ``pdfGeneratedAt`` — timestamp de fin de rendu.
* ``pdfErrorMessage`` — message d'erreur si FAILED (utile pour debug en prod).
* ``pdfTaskId``      — celery task id (UUID) pour cross-référencer les logs.

Downgrade
---------
Drop des 6 colonnes + index + enum. Pas de tentative de copier les données ;
les bulletins déjà générés restent accessibles via leur clé S3 (la table
"qui sait" qu'ils existent serait reconstruite par le worker au prochain
trigger).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_reports_async"
down_revision: str | Sequence[str] | None = "0010_attendance_partition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PDF_STATUS_VALUES = ("PENDING", "PROCESSING", "DONE", "FAILED")


def upgrade() -> None:
    # 1. Enum --------------------------------------------------------------
    pdf_status = postgresql.ENUM(
        *PDF_STATUS_VALUES,
        name="ReportCardPdfStatus",
        create_type=False,
    )
    pdf_status.create(op.get_bind(), checkfirst=True)

    # 2. Columns -----------------------------------------------------------
    op.add_column(
        "ReportCard",
        sa.Column(
            "pdfStatus",
            pdf_status,
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
    )
    op.add_column(
        "ReportCard",
        sa.Column("pdfS3Key", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "ReportCard",
        sa.Column("pdfSha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ReportCard",
        sa.Column("pdfGeneratedAt", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ReportCard",
        sa.Column("pdfErrorMessage", sa.Text(), nullable=True),
    )
    op.add_column(
        "ReportCard",
        sa.Column("pdfTaskId", sa.String(length=64), nullable=True),
    )

    # 3. Indexes -----------------------------------------------------------
    # Index sur pdfStatus pour scan rapide des PENDING (worker housekeeping
    # /  re-queue), filtré pour ne couvrir QUE les status "actifs" — DONE est
    # le cas dominant après un trimestre et n'a pas besoin d'index.
    op.create_index(
        "ix_ReportCard_pdfStatus",
        "ReportCard",
        ["pdfStatus"],
        postgresql_where=sa.text("\"pdfStatus\" IN ('PENDING','PROCESSING','FAILED')"),
    )


def downgrade() -> None:
    op.drop_index("ix_ReportCard_pdfStatus", table_name="ReportCard")
    op.drop_column("ReportCard", "pdfTaskId")
    op.drop_column("ReportCard", "pdfErrorMessage")
    op.drop_column("ReportCard", "pdfGeneratedAt")
    op.drop_column("ReportCard", "pdfSha256")
    op.drop_column("ReportCard", "pdfS3Key")
    op.drop_column("ReportCard", "pdfStatus")

    pdf_status = postgresql.ENUM(
        *PDF_STATUS_VALUES,
        name="ReportCardPdfStatus",
        create_type=False,
    )
    pdf_status.drop(op.get_bind(), checkfirst=True)
