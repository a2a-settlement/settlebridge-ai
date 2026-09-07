"""add submission compliance and score-history judge provenance

Revision ID: c7e4f5a61b20
Revises: 1be823f85d49
Create Date: 2026-09-07 15:50:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "c7e4f5a61b20"
down_revision: Union[str, None] = "1be823f85d49"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("compliance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("score_history", sa.Column("judge_model", sa.String(length=128), nullable=True))
    op.add_column(
        "score_history",
        sa.Column("quality_prompt_version", sa.String(length=64), nullable=True),
    )
    op.add_column("score_history", sa.Column("prompt_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("score_history", "prompt_hash")
    op.drop_column("score_history", "quality_prompt_version")
    op.drop_column("score_history", "judge_model")
    op.drop_column("submissions", "compliance")
