"""module 3 — attendance: partitionnement déclaratif PostgreSQL par mois

Revision ID: 0010_attendance_partition
Revises: 0009_census_normalization
Create Date: 2026-05-24

Pourquoi ?
----------
À l'échelle nationale (3M élèves × ~200 jours/an = 600M lignes/an), la table
``AttendanceRecord`` non partitionnée saturerait :
* les dashboards par école/classe (full scan sur des mois) ;
* les VACUUM/ANALYZE (verrous longs) ;
* les sauvegardes (un seul fichier monolithique).

PostgreSQL 16 propose un partitionnement déclaratif natif (sans extension
externe comme pg_partman) qui permet à la fois :
* le partition pruning automatique sur les requêtes filtrées par
  ``scannedAt`` ;
* des sauvegardes / TRUNCATE / drop d'archives partition par partition ;
* la possibilité d'ajouter de nouveaux espaces de stockage par partition
  pour les anciens trimestres.

Stratégie de migration (zero-downtime simplifiée pour dev / staging)
--------------------------------------------------------------------
1. Renommer ``AttendanceRecord`` → ``AttendanceRecord_legacy``.
2. Créer la nouvelle ``AttendanceRecord`` partitionnée par RANGE sur
   ``scannedAt``. La PK doit inclure la partition key → on passe en PK
   composite ``(id, scannedAt)``. ``id`` reste cuid → unicité globale
   préservée côté applicatif (Prisma générait déjà des cuid).
3. Créer 12 partitions initiales (mois courant + 11 futurs) plus une
   partition ``_default`` (catch-all pour dates absentes du range).
4. Créer les mêmes indexes que la table héritée — propagation auto vers
   chaque partition.
5. ``INSERT INTO ... SELECT *`` depuis la legacy.
6. ``DROP TABLE AttendanceRecord_legacy`` (CASCADE — aucune FK n'y pointe
   au moment du Module 3, vérifié dans les modèles census/schools).

Downgrade : on retire la nouvelle (CASCADE → toutes les partitions) et on
restore la legacy. Si la table legacy a déjà été supprimée (upgrade joué
puis dropée), le downgrade recrée juste une coquille vide non-partitionnée
identique au schéma initial — c'est le comportement attendu pour un rollback.
"""
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.modules.attendance.partitions import make_partition_sql

revision: str = "0010_attendance_partition"
down_revision: str | Sequence[str] | None = "0009_census_normalization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Mois de référence pour la création initiale : on prend la date du jour
# au moment où la migration tourne, et on prépare le mois courant + 11
# futurs (12 mois glissants en avance). Le job Celery ensure_future
# entretient la fenêtre par la suite.
INITIAL_FUTURE_MONTHS = 11


def _next_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _initial_partition_months() -> list[tuple[int, int]]:
    today = date.today().replace(day=1)
    months: list[tuple[int, int]] = []
    cursor = today
    for _ in range(INITIAL_FUTURE_MONTHS + 1):  # courant + N futurs
        months.append((cursor.year, cursor.month))
        cursor = _next_month(cursor)
    return months


def upgrade() -> None:
    # 1. Rename legacy --------------------------------------------------------
    op.execute('ALTER TABLE "AttendanceRecord" RENAME TO "AttendanceRecord_legacy"')
    # Les indexes legacy gardent les anciens noms ; on les renomme aussi pour
    # libérer les noms canoniques qu'on va recréer.
    op.execute(
        'ALTER INDEX IF EXISTS "ix_AttendanceRecord_schoolId_scannedAt" '
        'RENAME TO "ix_AttendanceRecord_legacy_schoolId_scannedAt"'
    )
    op.execute(
        'ALTER INDEX IF EXISTS "ix_AttendanceRecord_studentId_scannedAt" '
        'RENAME TO "ix_AttendanceRecord_legacy_studentId_scannedAt"'
    )
    op.execute(
        'ALTER INDEX IF EXISTS "ix_AttendanceRecord_teacherId_scannedAt" '
        'RENAME TO "ix_AttendanceRecord_legacy_teacherId_scannedAt"'
    )
    op.execute(
        'ALTER INDEX IF EXISTS "ix_AttendanceRecord_school_status_scannedAt" '
        'RENAME TO "ix_AttendanceRecord_legacy_school_status_scannedAt"'
    )
    op.execute(
        'ALTER INDEX IF EXISTS "ix_AttendanceRecord_personType_scannedAt" '
        'RENAME TO "ix_AttendanceRecord_legacy_personType_scannedAt"'
    )

    # 2. Create new partitioned parent ---------------------------------------
    # PK composite (id, scannedAt) — contrainte PostgreSQL : la partition key
    # doit faire partie de toute contrainte d'unicité (PK incluse).
    op.execute(
        """
        CREATE TABLE "AttendanceRecord" (
            "id" VARCHAR(30) NOT NULL,
            "personType" "PersonType" NOT NULL,
            "status" "AttendanceStatus" NOT NULL DEFAULT 'PRESENT',
            "scannedAt" TIMESTAMPTZ NOT NULL DEFAULT now(),
            "schoolId" VARCHAR(30) NOT NULL REFERENCES "School"("id"),
            "studentId" VARCHAR(30) REFERENCES "Student"("id"),
            "teacherId" VARCHAR(30) REFERENCES "Teacher"("id"),
            PRIMARY KEY ("id", "scannedAt")
        ) PARTITION BY RANGE ("scannedAt")
        """
    )

    # 3. Indexes propagés à chaque partition (auto via PARTITION BY) ---------
    op.execute(
        'CREATE INDEX "ix_AttendanceRecord_schoolId_scannedAt" '
        'ON "AttendanceRecord" ("schoolId", "scannedAt")'
    )
    op.execute(
        'CREATE INDEX "ix_AttendanceRecord_studentId_scannedAt" '
        'ON "AttendanceRecord" ("studentId", "scannedAt")'
    )
    op.execute(
        'CREATE INDEX "ix_AttendanceRecord_teacherId_scannedAt" '
        'ON "AttendanceRecord" ("teacherId", "scannedAt")'
    )
    op.execute(
        'CREATE INDEX "ix_AttendanceRecord_school_status_scannedAt" '
        'ON "AttendanceRecord" ("schoolId", "status", "scannedAt")'
    )
    op.execute(
        'CREATE INDEX "ix_AttendanceRecord_personType_scannedAt" '
        'ON "AttendanceRecord" ("personType", "scannedAt")'
    )

    # 4. Initial partitions (12 mois glissants + default catch-all) ----------
    for year, month in _initial_partition_months():
        op.execute(make_partition_sql(year, month))
    op.execute(
        'CREATE TABLE "AttendanceRecord_default" '
        'PARTITION OF "AttendanceRecord" DEFAULT'
    )

    # 5. Migrate data from legacy -------------------------------------------
    # Les colonnes sont identiques ; on les énumère pour rester explicite.
    op.execute(
        'INSERT INTO "AttendanceRecord" '
        '("id","personType","status","scannedAt","schoolId","studentId","teacherId") '
        'SELECT "id","personType","status","scannedAt","schoolId","studentId","teacherId" '
        'FROM "AttendanceRecord_legacy"'
    )

    # 6. Drop legacy --------------------------------------------------------
    op.execute('DROP TABLE "AttendanceRecord_legacy" CASCADE')


def downgrade() -> None:
    # On retire la table partitionnée (CASCADE → partitions enfants) et on
    # recrée la version non-partitionnée vide. Aucune tentative de copier
    # les données : un downgrade en prod doit être planifié manuellement.
    op.execute('DROP TABLE IF EXISTS "AttendanceRecord" CASCADE')

    op.create_table(
        "AttendanceRecord",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "personType",
            postgresql.ENUM(
                "STUDENT", "TEACHER", name="PersonType", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PRESENT", "LATE", "ABSENT",
                name="AttendanceStatus", create_type=False,
            ),
            nullable=False,
            server_default="PRESENT",
        ),
        sa.Column(
            "scannedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "schoolId",
            sa.String(length=30),
            sa.ForeignKey("School.id"),
            nullable=False,
        ),
        sa.Column(
            "studentId",
            sa.String(length=30),
            sa.ForeignKey("Student.id"),
            nullable=True,
        ),
        sa.Column(
            "teacherId",
            sa.String(length=30),
            sa.ForeignKey("Teacher.id"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_AttendanceRecord_schoolId_scannedAt",
        "AttendanceRecord",
        ["schoolId", "scannedAt"],
    )
    op.create_index(
        "ix_AttendanceRecord_studentId_scannedAt",
        "AttendanceRecord",
        ["studentId", "scannedAt"],
    )
    op.create_index(
        "ix_AttendanceRecord_teacherId_scannedAt",
        "AttendanceRecord",
        ["teacherId", "scannedAt"],
    )
