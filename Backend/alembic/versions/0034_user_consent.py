"""Module 5B — Consentement utilisateur + mentions légales.

Revision ID: 0034_user_consent
Revises: 0033_right_to_be_forgotten
Create Date: 2026-05-25

Pourquoi ?
----------
Loi 037/AN/2016 (Guinée) + RGPD imposent :

* L'information transparente de l'utilisateur sur les données collectées
  et leurs finalités à la 1ère connexion.
* Le recueil du consentement explicite (acte volontaire et tracé).
* Une politique de confidentialité accessible en permanence.

Modèle
------
``UserConsent`` trace l'acceptation d'une version donnée du contrat
d'utilisation par un utilisateur. Une version (ex: ``"2026-05-01"``)
correspond à une révision du document légal — si la politique évolue,
on incrémente la version et on redemande le consentement à la connexion
suivante.

* ``userId`` est ``UNIQUE`` : on garde la **dernière** acceptation
  (les anciennes ne servent à rien, l'audit est suffisant via
  ``acceptedAt`` + ``consentVersion``).
* ``ip`` + ``userAgent`` permettent de prouver l'acte de consentement
  en cas de contestation.

``User.consentVersion`` est un cache dénormalisé pour interroger sans
join sur ``UserConsent`` à chaque requête.

Index
-----
* ``(userId)`` — implicite via UNIQUE.
* ``(consentVersion)`` — audit conformité par version.

Downgrade
---------
Drop indexes + drop table + drop colonne ``User.consentVersion``.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034_user_consent"
down_revision: str | Sequence[str] | None = "0033_right_to_be_forgotten"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "UserConsent",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "userId",
            sa.String(length=30),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("consentVersion", sa.String(length=20), nullable=False),
        sa.Column(
            "acceptedAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("userAgent", sa.String(length=512), nullable=True),
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
        "ix_UserConsent_userId",
        "UserConsent",
        ["userId"],
    )
    op.create_index(
        "ix_UserConsent_consentVersion",
        "UserConsent",
        ["consentVersion"],
    )

    op.add_column(
        "User",
        sa.Column("consentVersion", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("User", "consentVersion")

    op.drop_index("ix_UserConsent_consentVersion", table_name="UserConsent")
    op.drop_index("ix_UserConsent_userId", table_name="UserConsent")
    op.drop_table("UserConsent")
