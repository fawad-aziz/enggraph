"""impact analysis reports

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14

Persisted change-impact reports (FR-09). The report JSON holds graph ids,
relationship paths, provenance and limitations text. No credentials and no raw
provider payloads are stored here.

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "impact_analyses",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(128), nullable=False),
        sa.Column("requested_by", sa.String(36), nullable=True),
        sa.Column("subject_id", sa.String(160), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("limit", sa.Integer(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_impact_analyses"),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name="fk_impact_analyses_requested_by",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_impact_analyses_organization_id", "impact_analyses", ["organization_id"])
    op.create_index("ix_impact_analyses_subject_id", "impact_analyses", ["subject_id"])


def downgrade() -> None:
    op.drop_table("impact_analyses")
