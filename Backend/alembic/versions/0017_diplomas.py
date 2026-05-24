"""module 11 — diplômes signés numériquement (Ed25519) avec vérification publique

Revision ID: 0017_diplomas
Revises: 0016_assistant
Create Date: 2026-05-24

Pourquoi ?
----------
Module 11 émet des diplômes nationaux (CEPE, BEPC, CFEE) signés
numériquement Ed25519 et vérifiables PUBLIQUEMENT (sans auth) via un
serial encodé dans un QR code. L'objectif business est anti-fraude
documentaire à l'échelle nationale : un recruteur peut scanner le QR et
voir en 1 clic si le diplôme est authentique, révoqué, ou inconnu.

Table unique ``Diploma`` :

* ``serial`` UNIQUE — identifiant publiquement vérifiable
  (format ``{TYPE}-{YEAR}-{8HEX}``).
* ``signature`` + ``payloadSha256`` + ``publicKeyFingerprint`` — preuve
  cryptographique. La signature est calculée sur le SHA-256 d'un payload
  JSON canonicalisé (RFC 8785 simplifié : sorted keys, no whitespace).
* ``status`` ∈ {DRAFT, ISSUED, REVOKED}. Un diplôme REVOKED reste en DB
  et la vérification publique l'affiche explicitement avec la raison.
* ``pdfS3Key`` nullable — le PDF est optionnel pour le MVP, la signature
  reste valable sans.

Indexes
-------
* ``serial`` UNIQUE — recherche publique en O(1).
* ``studentId`` — historique des diplômes d'un élève.
* ``status`` — KPIs nationaux (combien d'ISSUED, de REVOKED).
* ``(schoolId, status)`` — listing par école / directeur.
* ``(diplomaType, academicYearId)`` — agrégats par cohorte.

Downgrade
---------
Drop de la table + drop des deux enums (``DiplomaType``, ``DiplomaStatus``).
Module 10 (assistant) reste intact.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_diplomas"
down_revision: str | Sequence[str] | None = "0016_assistant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DIPLOMA_TYPE = postgresql.ENUM(
    "CEPE", "BEPC", "CFEE",
    name="DiplomaType", create_type=False,
)
DIPLOMA_STATUS = postgresql.ENUM(
    "DRAFT", "ISSUED", "REVOKED",
    name="DiplomaStatus", create_type=False,
)

_ALL_ENUMS = (DIPLOMA_TYPE, DIPLOMA_STATUS)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ALL_ENUMS:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "Diploma",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column("serial", sa.String(length=40), nullable=False),
        sa.Column("studentId", sa.String(length=30), nullable=False),
        sa.Column("diplomaType", DIPLOMA_TYPE, nullable=False),
        sa.Column("academicYearId", sa.String(length=30), nullable=True),
        sa.Column("schoolId", sa.String(length=30), nullable=False),
        sa.Column("examCenter", sa.String(length=200), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("mention", sa.String(length=40), nullable=True),
        sa.Column("issuedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payloadSha256", sa.String(length=64), nullable=True),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("publicKeyFingerprint", sa.String(length=64), nullable=True),
        sa.Column("pdfS3Key", sa.String(length=500), nullable=True),
        sa.Column(
            "status", DIPLOMA_STATUS,
            nullable=False, server_default="DRAFT",
        ),
        sa.Column("revokedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revokedReason", sa.Text(), nullable=True),
        sa.Column(
            "createdAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updatedAt", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["studentId"], ["Student.id"],
            name="fk_Diploma_studentId_Student",
        ),
        sa.ForeignKeyConstraint(
            ["schoolId"], ["School.id"],
            name="fk_Diploma_schoolId_School",
        ),
        sa.ForeignKeyConstraint(
            ["academicYearId"], ["SchoolYear.id"],
            name="fk_Diploma_academicYearId_SchoolYear",
        ),
        sa.UniqueConstraint("serial", name="uq_Diploma_serial"),
    )

    op.create_index("ix_Diploma_studentId", "Diploma", ["studentId"])
    op.create_index("ix_Diploma_status", "Diploma", ["status"])
    op.create_index(
        "ix_Diploma_schoolId_status",
        "Diploma", ["schoolId", "status"],
    )
    op.create_index(
        "ix_Diploma_diplomaType_academicYearId",
        "Diploma", ["diplomaType", "academicYearId"],
    )


def downgrade() -> None:
    op.drop_index("ix_Diploma_diplomaType_academicYearId", table_name="Diploma")
    op.drop_index("ix_Diploma_schoolId_status", table_name="Diploma")
    op.drop_index("ix_Diploma_status", table_name="Diploma")
    op.drop_index("ix_Diploma_studentId", table_name="Diploma")
    op.drop_table("Diploma")

    bind = op.get_bind()
    for enum_type in reversed(_ALL_ENUMS):
        enum_type.drop(bind, checkfirst=True)
