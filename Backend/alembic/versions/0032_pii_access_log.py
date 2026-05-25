"""Module 5C — Audit des accès PII (loi 037/AN/2016 Guinée + RGPD).

Revision ID: 0032_pii_access_log
Revises: 0031_investment_priority
Create Date: 2026-05-25

Pourquoi ?
----------
La loi guinéenne 037/AN/2016 sur la protection des données personnelles,
alignée sur les bonnes pratiques RGPD (Art. 30 — registre des activités
de traitement, Art. 32 — sécurité, traçabilité), exige de tracer TOUTE
consultation de données personnelles d'enfants mineurs et de leurs
représentants légaux. La table existante ``AuthAuditLog`` (Module 1.1)
trace les évènements d'authentification (login / logout / MFA / etc.) —
mais pas les accès en lecture aux fiches élèves / parents / santé.

But concret : si le ministère reçoit une demande "qui a consulté la
fiche médicale de mon enfant et quand ?", on doit pouvoir y répondre.

Modèle
------
``PiiAccessLog`` est append-only ; il n'a pas de ``updatedAt`` (chaque
ligne représente un évènement immuable). Une table dédiée — séparée
d'``AuthAuditLog`` — est nécessaire pour deux raisons :

* **Volumétrie** : un seul appel à ``GET /api/census/students`` peut
  générer 50+ lignes (une par élève visible). Au régime de croisière on
  vise plusieurs millions de lignes/mois.
* **Schéma** : on trace ``entityType`` / ``entityId`` / ``accessType``
  qui n'ont aucun sens dans le contexte authentification.

Index
-----
* ``(userId, accessedAt DESC)`` — "lister mes accès récents".
* ``(entityType, entityId, accessedAt DESC)`` — "qui a consulté la fiche
  de cet élève ?".
* ``(accessedAt)`` — purge par fenêtre temporelle (3 ans = 1095 jours).

Downgrade
---------
Drop indices + drop table + drop enums associés. Sans danger : la table
est purement append-only, aucun autre objet ne référence ces lignes.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0032_pii_access_log"
down_revision: str | Sequence[str] | None = "0031_investment_priority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PII_ENTITY_TYPE = postgresql.ENUM(
    "STUDENT",
    "PARENT",
    "HEALTH_VISIT",
    "VACCINATION",
    "ALLERGY",
    "INCIDENT",
    "STUDENT_TRANSFER",
    name="PiiEntityType",
    create_type=False,
)

PII_ACCESS_TYPE = postgresql.ENUM(
    "VIEW",
    "LIST",
    "EXPORT",
    name="PiiAccessType",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    PII_ENTITY_TYPE.create(bind, checkfirst=True)
    PII_ACCESS_TYPE.create(bind, checkfirst=True)

    op.create_table(
        "PiiAccessLog",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "userId",
            sa.String(length=30),
            sa.ForeignKey("User.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("userRole", sa.String(length=40), nullable=True),
        sa.Column("entityType", PII_ENTITY_TYPE, nullable=False),
        sa.Column("entityId", sa.String(length=30), nullable=False),
        sa.Column("accessType", PII_ACCESS_TYPE, nullable=False),
        sa.Column("endpoint", sa.String(length=200), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("userAgent", sa.String(length=512), nullable=True),
        sa.Column("requestId", sa.String(length=60), nullable=True),
        sa.Column(
            "metadataJson",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "accessedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_PiiAccessLog_userId_accessedAt",
        "PiiAccessLog",
        ["userId", "accessedAt"],
        postgresql_ops={"accessedAt": "DESC"},
    )
    op.create_index(
        "ix_PiiAccessLog_entity_accessedAt",
        "PiiAccessLog",
        ["entityType", "entityId", "accessedAt"],
        postgresql_ops={"accessedAt": "DESC"},
    )
    op.create_index(
        "ix_PiiAccessLog_accessedAt",
        "PiiAccessLog",
        ["accessedAt"],
    )


def downgrade() -> None:
    op.drop_index("ix_PiiAccessLog_accessedAt", table_name="PiiAccessLog")
    op.drop_index(
        "ix_PiiAccessLog_entity_accessedAt", table_name="PiiAccessLog"
    )
    op.drop_index(
        "ix_PiiAccessLog_userId_accessedAt", table_name="PiiAccessLog"
    )
    op.drop_table("PiiAccessLog")

    bind = op.get_bind()
    PII_ACCESS_TYPE.drop(bind, checkfirst=True)
    PII_ENTITY_TYPE.drop(bind, checkfirst=True)
