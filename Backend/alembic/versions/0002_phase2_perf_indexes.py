"""phase 2 — index complémentaires pour la performance Census

Revision ID: 0002_phase2_perf
Revises: 0001_initial
Create Date: 2026-05-05

À l'échelle 3M élèves / 200K enseignants, les index simples du schéma initial
sont insuffisants. On ajoute :

* Recherche par nom/prénom (FTS prep) avec un index trigram (pg_trgm)
* Index composites pour les filtres territoriaux + statut/genre
* Index sur classRoomId pour les jointures de dashboard
* Index sur le code de bulletin et le téléphone parent (lookups fréquents)

L'extension pg_trgm est créée si absente (gracieux si non disponible).
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0002_phase2_perf"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pg_trgm for fast LIKE/ILIKE on names (lookups + fuzzy search)
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS pg_trgm;
            RAISE NOTICE 'pg_trgm extension enabled.';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pg_trgm not installed at OS level — name search will use btree.';
        END $$;
        """
    )

    # --- Student composite indexes ---
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_Student_classRoomId" ON "Student" ("classRoomId")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_Student_school_class" '
        'ON "Student" ("schoolId", "classRoomId")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_Student_gender" ON "Student" ("gender")'
    )
    # Trigram indexes for ILIKE searches on names — only if pg_trgm exists
    op.execute(
        """
        DO $$
        BEGIN
            CREATE INDEX IF NOT EXISTS "ix_Student_lastName_trgm"
                ON "Student" USING gin (lower("lastName") gin_trgm_ops);
            CREATE INDEX IF NOT EXISTS "ix_Student_firstName_trgm"
                ON "Student" USING gin (lower("firstName") gin_trgm_ops);
        EXCEPTION WHEN undefined_object THEN
            NULL;
        END $$;
        """
    )

    # --- Teacher composite indexes ---
    op.execute(
        """
        DO $$
        BEGIN
            CREATE INDEX IF NOT EXISTS "ix_Teacher_lastName_trgm"
                ON "Teacher" USING gin (lower("lastName") gin_trgm_ops);
            CREATE INDEX IF NOT EXISTS "ix_Teacher_firstName_trgm"
                ON "Teacher" USING gin (lower("firstName") gin_trgm_ops);
        EXCEPTION WHEN undefined_object THEN
            NULL;
        END $$;
        """
    )

    # --- School geo + composite ---
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_School_lat_lon" '
        'ON "School" ("latitude", "longitude") WHERE "latitude" IS NOT NULL'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_School_region_prefecture" '
        'ON "School" ("regionId", "prefecture")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_School_region_commune" '
        'ON "School" ("regionId", "commune")'
    )

    # --- AttendanceRecord composite for dashboard queries ---
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_AttendanceRecord_school_status_scannedAt" '
        'ON "AttendanceRecord" ("schoolId", "status", "scannedAt")'
    )
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_AttendanceRecord_personType_scannedAt" '
        'ON "AttendanceRecord" ("personType", "scannedAt")'
    )

    # --- Parent quick lookup ---
    op.execute(
        """
        DO $$
        BEGIN
            CREATE INDEX IF NOT EXISTS "ix_Parent_phone_trgm"
                ON "Parent" USING gin ("phone" gin_trgm_ops);
        EXCEPTION WHEN undefined_object THEN
            NULL;
        END $$;
        """
    )

    # --- Grade composite for student period lookups ---
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_Grade_student_subject_period" '
        'ON "Grade" ("studentId", "subjectId", "periodId")'
    )

    # --- ReportCard verification code lookup (already unique, but explicit btree) ---
    # (verificationCode already has a unique constraint → no extra index needed)


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS "ix_Grade_student_subject_period"')
    op.execute('DROP INDEX IF EXISTS "ix_Parent_phone_trgm"')
    op.execute('DROP INDEX IF EXISTS "ix_AttendanceRecord_personType_scannedAt"')
    op.execute('DROP INDEX IF EXISTS "ix_AttendanceRecord_school_status_scannedAt"')
    op.execute('DROP INDEX IF EXISTS "ix_School_region_commune"')
    op.execute('DROP INDEX IF EXISTS "ix_School_region_prefecture"')
    op.execute('DROP INDEX IF EXISTS "ix_School_lat_lon"')
    op.execute('DROP INDEX IF EXISTS "ix_Teacher_firstName_trgm"')
    op.execute('DROP INDEX IF EXISTS "ix_Teacher_lastName_trgm"')
    op.execute('DROP INDEX IF EXISTS "ix_Student_firstName_trgm"')
    op.execute('DROP INDEX IF EXISTS "ix_Student_lastName_trgm"')
    op.execute('DROP INDEX IF EXISTS "ix_Student_gender"')
    op.execute('DROP INDEX IF EXISTS "ix_Student_school_class"')
    op.execute('DROP INDEX IF EXISTS "ix_Student_classRoomId"')
    # pg_trgm extension intentionally NOT dropped — may be used elsewhere
