"""add partial approval holdback fields

Revision ID: a1c9e3f50b22
Revises: 2f17560dd61d
Create Date: 2026-03-08 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a1c9e3f50b22'
down_revision: Union[str, None] = '2f17560dd61d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE submissionstatus ADD VALUE IF NOT EXISTS 'PARTIALLY_APPROVED'")

    op.add_column('submissions', sa.Column('score', sa.Integer(), nullable=True))
    op.add_column('submissions', sa.Column('release_percent', sa.Integer(), nullable=True))
    op.add_column('submissions', sa.Column('efficacy_check_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('submissions', sa.Column('efficacy_criteria', sa.Text(), nullable=True))
    op.add_column('submissions', sa.Column('efficacy_score', sa.Integer(), nullable=True))
    op.add_column('submissions', sa.Column('efficacy_reviewed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('submissions', 'efficacy_reviewed_at')
    op.drop_column('submissions', 'efficacy_score')
    op.drop_column('submissions', 'efficacy_criteria')
    op.drop_column('submissions', 'efficacy_check_at')
    op.drop_column('submissions', 'release_percent')
    op.drop_column('submissions', 'score')
