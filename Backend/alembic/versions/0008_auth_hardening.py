"""module 1 — auth hardening (MFA, refresh sessions, password history, audit)

Revision ID: 0008_auth_hardening
Revises: 0007_phase13bis
Create Date: 2026-05-23

Adds:
* `User.mfaRequired`, `User.mfaEnabled`, `User.passwordChangedAt` columns
* New tables: `MfaCredential`, `PasswordHistory`, `RefreshTokenSession`,
  `AuthAuditLog`, `PasswordResetToken`
* Indexes used by Module 1 (auth audit lookups, password history,
  refresh-session uniqueness, password-reset-token uniqueness)
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_auth_hardening"
down_revision: str | Sequence[str] | None = "0007_phase13bis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- User: add Module 1 columns ----------------------------
    op.add_column(
        "User",
        sa.Column(
            "mfaRequired",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "User",
        sa.Column(
            "mfaEnabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "User",
        sa.Column(
            "passwordChangedAt",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # ---------------- MfaCredential -----------------------------------------
    op.create_table(
        "MfaCredential",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "userId",
            sa.String(length=30),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("secret", sa.String(), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("verifiedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "recoveryCodesHashed",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
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

    # ---------------- PasswordHistory ---------------------------------------
    op.create_table(
        "PasswordHistory",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "userId",
            sa.String(length=30),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("passwordHash", sa.String(), nullable=False),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_PasswordHistory_userId_createdAt",
        "PasswordHistory",
        ["userId", "createdAt"],
    )

    # ---------------- RefreshTokenSession -----------------------------------
    op.create_table(
        "RefreshTokenSession",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "userId",
            sa.String(length=30),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tokenHash", sa.String(length=64), nullable=False, unique=True
        ),
        sa.Column("userAgent", sa.String(), nullable=True),
        sa.Column("ipAddress", sa.String(), nullable=True),
        sa.Column("lastUsedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revokedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revokedReason", sa.String(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_RefreshTokenSession_userId_createdAt",
        "RefreshTokenSession",
        ["userId", "createdAt"],
    )

    # ---------------- AuthAuditLog ------------------------------------------
    op.create_table(
        "AuthAuditLog",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "userId",
            sa.String(length=30),
            sa.ForeignKey("User.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("ipAddress", sa.String(), nullable=True),
        sa.Column("userAgent", sa.String(), nullable=True),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column(
            "success", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("failureReason", sa.String(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_AuthAuditLog_userId_createdAt",
        "AuthAuditLog",
        ["userId", "createdAt"],
    )
    op.create_index(
        "ix_AuthAuditLog_email_createdAt",
        "AuthAuditLog",
        ["email", "createdAt"],
    )

    # ---------------- PasswordResetToken ------------------------------------
    op.create_table(
        "PasswordResetToken",
        sa.Column("id", sa.String(length=30), primary_key=True),
        sa.Column(
            "userId",
            sa.String(length=30),
            sa.ForeignKey("User.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tokenHash", sa.String(length=64), nullable=False, unique=True
        ),
        sa.Column("expiresAt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("usedAt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ipAddress", sa.String(), nullable=True),
        sa.Column(
            "createdAt",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("PasswordResetToken")
    op.drop_index("ix_AuthAuditLog_email_createdAt", table_name="AuthAuditLog")
    op.drop_index("ix_AuthAuditLog_userId_createdAt", table_name="AuthAuditLog")
    op.drop_table("AuthAuditLog")
    op.drop_index(
        "ix_RefreshTokenSession_userId_createdAt",
        table_name="RefreshTokenSession",
    )
    op.drop_table("RefreshTokenSession")
    op.drop_index(
        "ix_PasswordHistory_userId_createdAt", table_name="PasswordHistory"
    )
    op.drop_table("PasswordHistory")
    op.drop_table("MfaCredential")
    op.drop_column("User", "passwordChangedAt")
    op.drop_column("User", "mfaEnabled")
    op.drop_column("User", "mfaRequired")
