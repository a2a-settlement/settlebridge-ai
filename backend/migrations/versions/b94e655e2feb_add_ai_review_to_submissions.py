"""add ai_review to submissions

Revision ID: b94e655e2feb
Revises: c4a7f1e89b30
Create Date: 2026-03-13 14:46:29.240312
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b94e655e2feb'
down_revision: Union[str, None] = 'c4a7f1e89b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='submissions' AND column_name='ai_review'"
    ))
    if not result.fetchone():
        op.add_column('submissions', sa.Column(
            'ai_review',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ))


def downgrade() -> None:
    op.drop_column('submissions', 'ai_review')
