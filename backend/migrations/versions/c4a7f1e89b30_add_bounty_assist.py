"""add bounty assist

Revision ID: c4a7f1e89b30
Revises: a1c9e3f50b22
Create Date: 2026-03-09 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c4a7f1e89b30'
down_revision: Union[str, None] = 'a1c9e3f50b22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

assistsessionstatus_enum = postgresql.ENUM(
    'ACTIVE', 'DRAFT_READY', 'FINALIZED', 'ABANDONED',
    name='assistsessionstatus', create_type=False
)


def upgrade() -> None:
    assistsessionstatus_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'assist_sessions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('status', assistsessionstatus_enum, nullable=False),
        sa.Column('messages', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('bounty_draft', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('settlement_structure', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('turn_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('finalized_bounty_id', sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['finalized_bounty_id'], ['bounties.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.add_column('bounties', sa.Column(
        'settlement_structure',
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    ))


def downgrade() -> None:
    op.drop_column('bounties', 'settlement_structure')
    op.drop_table('assist_sessions')
    assistsessionstatus_enum.drop(op.get_bind(), checkfirst=True)
