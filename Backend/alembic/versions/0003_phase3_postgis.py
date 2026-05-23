"""phase 3 — PostGIS pour la carte scolaire dynamique

Revision ID: 0003_phase3_postgis
Revises: 0002_phase2_perf
Create Date: 2026-05-05

Cette migration RENFORCE les exigences PostGIS :
* Vérifie que l'extension PostGIS est installée (échec sinon)
* Ajoute la colonne `geom geography(Point, 4326)` à School
* Backfill depuis latitude/longitude existants
* Crée un trigger pour synchroniser geom à chaque update lat/lon
* Index GIST sur geom (toutes les requêtes spatiales en dépendent)
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0003_phase3_postgis"
down_revision: str | Sequence[str] | None = "0002_phase2_perf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Hard-require PostGIS in Phase 3 — cartography depends on it.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis') THEN
                RAISE EXCEPTION 'PostGIS extension is required for Phase 3. '
                                'Install postgis package on the host (e.g. brew install postgis), '
                                'then run: CREATE EXTENSION postgis;';
            END IF;
        END $$;
        """
    )

    # Add geom column on School
    op.execute(
        'ALTER TABLE "School" ADD COLUMN IF NOT EXISTS "geom" geography(Point, 4326)'
    )

    # Backfill from existing latitude/longitude
    op.execute(
        """
        UPDATE "School"
        SET "geom" = ST_SetSRID(ST_MakePoint("longitude", "latitude"), 4326)::geography
        WHERE "latitude" IS NOT NULL AND "longitude" IS NOT NULL AND "geom" IS NULL;
        """
    )

    # GIST index for spatial queries
    op.execute(
        'CREATE INDEX IF NOT EXISTS "ix_School_geom_gist" ON "School" USING GIST ("geom")'
    )

    # Trigger function: sync geom from latitude/longitude on insert/update
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_school_geom() RETURNS TRIGGER AS $$
        BEGIN
            IF NEW."latitude" IS NOT NULL AND NEW."longitude" IS NOT NULL THEN
                NEW."geom" := ST_SetSRID(ST_MakePoint(NEW."longitude", NEW."latitude"), 4326)::geography;
            ELSE
                NEW."geom" := NULL;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute('DROP TRIGGER IF EXISTS trg_school_sync_geom ON "School"')
    op.execute(
        """
        CREATE TRIGGER trg_school_sync_geom
        BEFORE INSERT OR UPDATE OF "latitude", "longitude" ON "School"
        FOR EACH ROW EXECUTE FUNCTION sync_school_geom();
        """
    )


def downgrade() -> None:
    op.execute('DROP TRIGGER IF EXISTS trg_school_sync_geom ON "School"')
    op.execute('DROP FUNCTION IF EXISTS sync_school_geom()')
    op.execute('DROP INDEX IF EXISTS "ix_School_geom_gist"')
    op.execute('ALTER TABLE "School" DROP COLUMN IF EXISTS "geom"')
    # PostGIS extension intentionally NOT dropped (other apps may use it)
