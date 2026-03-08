"""add service_contracts and snapshots

Revision ID: d38eb84ad01f
Revises: 748b5081dace
Create Date: 2026-03-08 17:28:26.659239
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'd38eb84ad01f'
down_revision: Union[str, None] = '748b5081dace'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

provenancetier_enum = postgresql.ENUM('TIER1_SELF_DECLARED', 'TIER2_SIGNED', 'TIER3_VERIFIABLE', name='provenancetier', create_type=False)
contractstatus_enum = postgresql.ENUM('DRAFT', 'ACTIVE', 'PAUSED', 'CANCELLED', 'COMPLETED', name='contractstatus', create_type=False)
snapshotstatus_enum = postgresql.ENUM('PENDING', 'DELIVERED', 'APPROVED', 'REJECTED', 'MISSED', 'DISPUTED', name='snapshotstatus', create_type=False)


def upgrade() -> None:
    contractstatus_enum.create(op.get_bind(), checkfirst=True)
    snapshotstatus_enum.create(op.get_bind(), checkfirst=True)

    op.create_table('service_contracts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('requester_id', sa.UUID(), nullable=False),
    sa.Column('agent_user_id', sa.UUID(), nullable=False),
    sa.Column('agent_exchange_bot_id', sa.String(length=255), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('category_id', sa.UUID(), nullable=True),
    sa.Column('acceptance_criteria', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('provenance_tier', provenancetier_enum, nullable=False),
    sa.Column('reward_per_snapshot', sa.Integer(), nullable=False),
    sa.Column('schedule', sa.String(length=100), nullable=False),
    sa.Column('schedule_description', sa.String(length=255), nullable=False),
    sa.Column('max_snapshots', sa.Integer(), nullable=True),
    sa.Column('grace_period_hours', sa.Integer(), nullable=False),
    sa.Column('auto_approve', sa.Boolean(), nullable=False),
    sa.Column('status', contractstatus_enum, nullable=False),
    sa.Column('group_id', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['agent_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ),
    sa.ForeignKeyConstraint(['requester_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('contract_id', sa.UUID(), nullable=False),
    sa.Column('cycle_number', sa.Integer(), nullable=False),
    sa.Column('escrow_id', sa.String(length=255), nullable=True),
    sa.Column('deliverable', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('provenance', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', snapshotstatus_enum, nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reviewer_notes', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['contract_id'], ['service_contracts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('snapshots')
    op.drop_table('service_contracts')
    snapshotstatus_enum.drop(op.get_bind(), checkfirst=True)
    contractstatus_enum.drop(op.get_bind(), checkfirst=True)
