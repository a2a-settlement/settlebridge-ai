"""add training loop

Revision ID: e9f2a4b76c1d
Revises: 62eb736b04d7, 2f17560dd61d, c4a7f1e89b30
Create Date: 2026-04-02 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'e9f2a4b76c1d'
down_revision: Union[str, None] = '62eb736b04d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_trainingrunstatus = postgresql.ENUM(
    'RUNNING', 'COMPLETED', 'CANCELLED',
    name='trainingrunstatus', create_type=False,
)
_scoremode = postgresql.ENUM(
    'TRAINING', 'PRODUCTION',
    name='scoremode', create_type=False,
)
_bountymode = postgresql.ENUM(
    'TRAINING', 'PRODUCTION',
    name='bountymode', create_type=False,
)


def upgrade() -> None:
    # Create enum types explicitly (create_type=False above prevents double-creation)
    _trainingrunstatus.create(op.get_bind(), checkfirst=True)
    _scoremode.create(op.get_bind(), checkfirst=True)
    _bountymode.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'training_runs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('bounty_id', sa.UUID(), nullable=False),
        sa.Column('agent_user_id', sa.UUID(), nullable=False),
        sa.Column('status', _trainingrunstatus, nullable=False, server_default='RUNNING'),
        sa.Column('max_iterations', sa.Integer(), nullable=False),
        sa.Column('stake_budget', sa.Integer(), nullable=False),
        sa.Column('stake_spent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('score_threshold', sa.Float(), nullable=False),
        sa.Column('task_type', sa.String(length=100), nullable=False),
        sa.Column('bounty_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('iterations_completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['agent_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['bounty_id'], ['bounties.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'score_history',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('agent_user_id', sa.UUID(), nullable=False),
        sa.Column('bounty_id', sa.UUID(), nullable=False),
        sa.Column('training_run_id', sa.UUID(), nullable=True),
        sa.Column('task_type', sa.String(length=100), nullable=True),
        sa.Column('numeric_score', sa.Float(), nullable=False),
        sa.Column('reasoning', sa.Text(), nullable=True),
        sa.Column('diagnostics', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('mode', _scoremode, nullable=False, server_default='TRAINING'),
        sa.Column('provenance_hash', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['agent_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['bounty_id'], ['bounties.id']),
        sa.ForeignKeyConstraint(['training_run_id'], ['training_runs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_score_history_training_run_id', 'score_history', ['training_run_id'])
    op.create_index('ix_score_history_agent_user_id', 'score_history', ['agent_user_id'])

    op.create_table(
        'training_transcripts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('training_run_id', sa.UUID(), nullable=False),
        sa.Column('agent_display_id', sa.String(length=255), nullable=False),
        sa.Column('bounty_id', sa.UUID(), nullable=False),
        sa.Column('total_iterations', sa.Integer(), nullable=False),
        sa.Column('total_stake_spent', sa.Integer(), nullable=False),
        sa.Column('final_training_ema', sa.Float(), nullable=False),
        sa.Column('merkle_root', sa.String(length=255), nullable=False),
        sa.Column('signed_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['bounty_id'], ['bounties.id']),
        sa.ForeignKeyConstraint(['training_run_id'], ['training_runs.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('training_run_id'),
    )

    # Add mode column to bounties
    op.add_column(
        'bounties',
        sa.Column('mode', _bountymode, nullable=False, server_default='PRODUCTION'),
    )

    # Add training_run_id FK to claims
    op.add_column(
        'claims',
        sa.Column('training_run_id', sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        'fk_claims_training_run_id',
        'claims', 'training_runs',
        ['training_run_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_claims_training_run_id', 'claims', type_='foreignkey')
    op.drop_column('claims', 'training_run_id')
    op.drop_column('bounties', 'mode')
    op.drop_table('training_transcripts')
    op.drop_index('ix_score_history_agent_user_id', table_name='score_history')
    op.drop_index('ix_score_history_training_run_id', table_name='score_history')
    op.drop_table('score_history')
    op.drop_table('training_runs')
    op.execute("DROP TYPE IF EXISTS trainingrunstatus")
    op.execute("DROP TYPE IF EXISTS scoremode")
    op.execute("DROP TYPE IF EXISTS bountymode")
