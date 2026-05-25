"""Module 5D — Droit à l'oubli (anonymisation post-sortie d'élève).

Revision ID: 0033_right_to_be_forgotten
Revises: 0032_pii_access_log
Create Date: 2026-05-25

Pourquoi ?
----------
Loi 037/AN/2016 (Guinée) + RGPD Art. 17. Quand un élève quitte
définitivement le système (déménagement à l'étranger, décès, exclusion),
le ministère a une obligation de droit à l'oubli sous 2 ans : retirer
les données nominatives tout en préservant les **agrégats statistiques**
(Module 1A — Enrollment.count par école/année/genre/niveau) pour ne pas
casser les indicateurs IIPE rétrospectifs.

Modèle
------
``ErasureRequest`` est une demande tracée, à 4 états :
    PENDING → GRACE_PERIOD → EXECUTED
                         ↘
                          CANCELLED

* On marque la demande directement en GRACE_PERIOD (PENDING n'est qu'un
  défaut DB de précaution si une migration ouvre le statut avant la
  création applicative).
* ``gracePeriodUntil`` = ``now + 30 days``. Avant cette date,
  l'opération est réversible : on peut annuler (CANCELLED).
* Après ``gracePeriodUntil``, le worker ``execute_pending_erasures_task``
  (beat quotidien 04:00 UTC) bascule en EXECUTED et appelle
  l'anonymizer.

Index
-----
* ``(status, gracePeriodUntil)`` — scan rapide du worker batch.
* ``(studentId)`` — vérifier qu'il n'existe pas déjà une demande
  active pour cet élève (uniqueness logique).
* ``(requestedAt DESC)`` — listing administratif.

FK ``studentId`` est ``ON DELETE SET NULL`` : après EXECUTED le student
peut être conservé en table (anonymisé) ou supprimé selon politique
locale ; dans les 2 cas la trace de la demande reste.

Downgrade
---------
Drop indexes + drop table + drop enums. Sans danger : aucune autre
table ne référence ces lignes (la FK est dans le sens inverse).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0033_right_to_be_forgotten"
down_revision: str | Sequence[str] | None = "0032_pii_access_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ERASURE_REASON = postgresql.ENUM(
    "LEFT_COUNTRY",
    "DECEASED",
    "EXCLUDED",
    "OTHER",
    name="ErasureReason",
    create_type=False,
)

ERASURE_STATUS = postgresql.ENUM(
    "PENDING",
    "GRACE_PERIOD",
    "EXECUTED",
    "CANCELLED",
    name="ErasureStatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    ERASURE_REASON.create(bind, checkfirst=True)
    ERASURE_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "ErasureRequest",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "studentId",
            sa.String(length=30),
            sa.ForeignKey("Student.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", ERASURE_REASON, nullable=False),
        sa.Column("reasonDetails", sa.Text(), nullable=True),
        sa.Column(
            "requestedById",
            sa.String(length=30),
            sa.ForeignKey("User.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "requestedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "status",
            ERASURE_STATUS,
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "gracePeriodUntil",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "executedAt",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "executedById",
            sa.String(length=30),
            sa.ForeignKey("User.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "cancelledById",
            sa.String(length=30),
            sa.ForeignKey("User.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "cancelledAt",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("cancellationReason", sa.Text(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_ErasureRequest_status_gracePeriodUntil",
        "ErasureRequest",
        ["status", "gracePeriodUntil"],
    )
    op.create_index(
        "ix_ErasureRequest_studentId",
        "ErasureRequest",
        ["studentId"],
    )
    op.create_index(
        "ix_ErasureRequest_requestedAt",
        "ErasureRequest",
        ["requestedAt"],
        postgresql_ops={"requestedAt": "DESC"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ErasureRequest_requestedAt",
        table_name="ErasureRequest",
    )
    op.drop_index(
        "ix_ErasureRequest_studentId",
        table_name="ErasureRequest",
    )
    op.drop_index(
        "ix_ErasureRequest_status_gracePeriodUntil",
        table_name="ErasureRequest",
    )
    op.drop_table("ErasureRequest")

    bind = op.get_bind()
    ERASURE_STATUS.drop(bind, checkfirst=True)
    ERASURE_REASON.drop(bind, checkfirst=True)
