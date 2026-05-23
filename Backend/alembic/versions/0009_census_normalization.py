"""module 2 — census normalization & deduplication infra

Revision ID: 0009_census_normalization
Revises: 0008_auth_hardening
Create Date: 2026-05-24

Ajoute :
* Index GIN pg_trgm sur ``Student.guardianPhone`` pour les recherches de
  doublons par téléphone (utile quand la même famille a plusieurs élèves
  enregistrés avec un téléphone légèrement différent — espaces, etc.).

Pas de modification de données existantes : c'est purement un index de
performance pour le service de dédoublonnage.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0009_census_normalization"
down_revision: str | Sequence[str] | None = "0008_auth_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Index trigram sur guardianPhone — n'échoue PAS si pg_trgm n'est pas
    # installé au niveau OS (le DO $$ ... EXCEPTION ... END $$ avale l'erreur).
    op.execute(
        """
        DO $$
        BEGIN
            CREATE INDEX IF NOT EXISTS "ix_Student_guardianPhone_trgm"
                ON "Student" USING gin ("guardianPhone" gin_trgm_ops);
        EXCEPTION WHEN undefined_object THEN
            RAISE NOTICE 'pg_trgm not installed — index Student.guardianPhone_trgm skipped.';
        END $$;
        """
    )


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS "ix_Student_guardianPhone_trgm"')
