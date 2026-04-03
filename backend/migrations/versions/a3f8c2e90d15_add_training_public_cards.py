"""add training public cards

Revision ID: a3f8c2e90d15
Revises: e9f2a4b76c1d
Create Date: 2026-04-03

Adds:
  - claims.virtual_escrow_id  — persists the virtual escrow created at claim
    time so the submission handler can always find it even after
    bounty.escrow_id is cleared between iterations
  - training_runs.public      — opt-in flag for public progression card
  - training_runs.public_title — optional display title for the card
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3f8c2e90d15"
down_revision = "e9f2a4b76c1d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("claims", sa.Column("virtual_escrow_id", sa.String(255), nullable=True))
    op.add_column(
        "training_runs",
        sa.Column("public", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("training_runs", sa.Column("public_title", sa.String(500), nullable=True))
    op.create_index("ix_training_runs_public", "training_runs", ["public"])


def downgrade() -> None:
    op.drop_index("ix_training_runs_public", table_name="training_runs")
    op.drop_column("training_runs", "public_title")
    op.drop_column("training_runs", "public")
    op.drop_column("claims", "virtual_escrow_id")
