"""add training loop: score_history, training_runs, training_transcripts, bounty mode

Revision ID: e5f2a1c38d90
Revises: 62eb736b04d7
Create Date: 2026-04-02 00:00:00.000000

Safe on existing production schemas:
- New tables have no mandatory FK constraints to rows that might not exist.
- The ``mode`` column on ``bounties`` uses a server-side default so all
  existing rows are backfilled to ``'production'`` without a data migration.
- No existing columns are modified or dropped.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f2a1c38d90"
down_revision: Union[str, None] = "62eb736b04d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create PostgreSQL enum types
    # ------------------------------------------------------------------
    bountymode = postgresql.ENUM(
        "production", "training", name="bountymode", create_type=False
    )
    bountymode.create(op.get_bind(), checkfirst=True)

    trainingrunstatus = postgresql.ENUM(
        "running", "completed", "exhausted", name="trainingrunstatus", create_type=False
    )
    trainingrunstatus.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # 2. Create training_runs table (before score_history which FKs to it)
    # ------------------------------------------------------------------
    op.create_table(
        "training_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column(
            "target_bounty_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bounties.id"),
            nullable=False,
        ),
        sa.Column("task_type", sa.String(255), nullable=True),
        sa.Column("max_iterations", sa.Integer(), nullable=False),
        sa.Column("stake_budget", sa.Integer(), nullable=False),
        sa.Column("stake_spent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score_threshold", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("running", "completed", "exhausted", name="trainingrunstatus"),
            nullable=False,
            server_default="running",
        ),
        sa.Column("bounty_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_training_runs_agent_id", "training_runs", ["agent_id"])

    # ------------------------------------------------------------------
    # 3. Create score_history table
    # ------------------------------------------------------------------
    op.create_table(
        "score_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column(
            "bounty_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bounties.id"),
            nullable=False,
        ),
        sa.Column("task_type", sa.String(255), nullable=True),
        sa.Column("numeric_score", sa.Float(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("diagnostics", postgresql.JSONB(), nullable=True),
        sa.Column(
            "mode",
            sa.Enum("production", "training", name="bountymode"),
            nullable=False,
            server_default="production",
        ),
        sa.Column(
            "training_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("training_runs.id"),
            nullable=True,
        ),
        sa.Column("provenance_hash", sa.String(64), nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_score_history_agent_id", "score_history", ["agent_id"])
    op.create_index("ix_score_history_bounty_id", "score_history", ["bounty_id"])
    op.create_index("ix_score_history_task_type", "score_history", ["task_type"])
    op.create_index("ix_score_history_mode", "score_history", ["mode"])
    op.create_index(
        "ix_score_history_training_run_id", "score_history", ["training_run_id"]
    )
    op.create_index("ix_score_history_timestamp", "score_history", ["timestamp"])

    # ------------------------------------------------------------------
    # 4. Create training_transcripts table
    # ------------------------------------------------------------------
    op.create_table(
        "training_transcripts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "training_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("training_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column(
            "bounty_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bounties.id"),
            nullable=False,
        ),
        sa.Column("total_iterations", sa.Integer(), nullable=False),
        sa.Column("total_stake_spent", sa.Integer(), nullable=False),
        sa.Column("final_production_ema", sa.Float(), nullable=True),
        sa.Column("final_training_ema", sa.Float(), nullable=True),
        sa.Column("merkle_root", sa.String(64), nullable=True),
        sa.Column("signed_payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_training_transcripts_agent_id", "training_transcripts", ["agent_id"]
    )

    # ------------------------------------------------------------------
    # 5. Add mode column to bounties (server default → safe on live data)
    # ------------------------------------------------------------------
    op.add_column(
        "bounties",
        sa.Column(
            "mode",
            sa.Enum("production", "training", name="bountymode"),
            nullable=False,
            server_default="production",
        ),
    )


def downgrade() -> None:
    # Remove mode column from bounties
    op.drop_column("bounties", "mode")

    # Drop new tables in reverse dependency order
    op.drop_table("training_transcripts")
    op.drop_table("score_history")
    op.drop_table("training_runs")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS trainingrunstatus")
    op.execute("DROP TYPE IF EXISTS bountymode")
