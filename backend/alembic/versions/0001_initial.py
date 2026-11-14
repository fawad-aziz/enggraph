"""initial schema: users, sessions, org/connector config, jobs, checkpoints, audit

Revision ID: 0001
Revises:
Create Date: 2026-11-14

Schema rules mirrored from enggraph/persistence/models.py:
- users.password_hash (PBKDF2), sessions.token_hash (SHA-256) — raw
  credentials are not stored anywhere in this schema.
- connector_configs has a `pat_secret_ref` (reference to where the PAT lives),
  not a PAT column.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = (
    "audit_records",
    "sync_checkpoints",
    "job_events",
    "sync_jobs",
    "sessions",
    "users",
    "connector_configs",
    "organization_configs",
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_organization_id", "users", ["organization_id"], unique=False)

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_sessions_user_id", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"], unique=False)
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)

    op.create_table(
        "organization_configs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("max_depth", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_configs"),
    )
    op.create_index(
        "ix_organization_configs_organization_id",
        "organization_configs",
        ["organization_id"],
        unique=True,
    )

    op.create_table(
        "connector_configs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("ado_organization", sa.String(128), nullable=False),
        sa.Column("pat_secret_ref", sa.String(128), nullable=False),
        sa.Column("project_names", sa.JSON(), nullable=False),
        sa.Column("page_size", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_validation_ok", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_connector_configs"),
    )
    op.create_index(
        "ix_connector_configs_organization_id",
        "connector_configs",
        ["organization_id"],
        unique=False,
    )

    op.create_table(
        "sync_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("connector_config_id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.String(36), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("parent_job_id", sa.String(36), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("nodes_written", sa.Integer(), nullable=False),
        sa.Column("edges_written", sa.Integer(), nullable=False),
        sa.Column("projects_completed", sa.Integer(), nullable=False),
        sa.Column("projects_planned", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_sync_jobs"),
        sa.ForeignKeyConstraint(
            ["connector_config_id"],
            ["connector_configs.id"],
            name="fk_sync_jobs_connector_config_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name="fk_sync_jobs_requested_by",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_job_id"],
            ["sync_jobs.id"],
            name="fk_sync_jobs_parent_job_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_sync_jobs_organization_id", "sync_jobs", ["organization_id"])
    op.create_index("ix_sync_jobs_connector_config_id", "sync_jobs", ["connector_config_id"])
    op.create_index("ix_sync_jobs_state", "sync_jobs", ["state"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_events"),
        sa.UniqueConstraint("job_id", "seq", name="uq_job_events_job_seq"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["sync_jobs.id"], name="fk_job_events_job_id", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"])

    op.create_table(
        "sync_checkpoints",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("connector_config_id", sa.String(36), nullable=False),
        sa.Column("scope", sa.String(160), nullable=False),
        sa.Column("provider_version", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.String(40), nullable=True),
        sa.Column("last_full_pass_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_full_pass_job_id", sa.String(36), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sync_checkpoints"),
        sa.UniqueConstraint(
            "organization_id",
            "connector_config_id",
            "scope",
            name="uq_sync_checkpoints_org_connector_scope",
        ),
        sa.ForeignKeyConstraint(
            ["connector_config_id"],
            ["connector_configs.id"],
            name="fk_sync_checkpoints_connector_config_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["last_full_pass_job_id"],
            ["sync_jobs.id"],
            name="fk_sync_checkpoints_last_full_pass_job_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_sync_checkpoints_organization_id", "sync_checkpoints", ["organization_id"])
    op.create_index(
        "ix_sync_checkpoints_connector_config_id",
        "sync_checkpoints",
        ["connector_config_id"],
    )

    op.create_table(
        "audit_records",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(160), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_records"),
    )
    op.create_index("ix_audit_records_organization_id", "audit_records", ["organization_id"])


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
