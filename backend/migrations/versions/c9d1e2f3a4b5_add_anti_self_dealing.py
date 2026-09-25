"""add anti-self-dealing principal tracking and diversity metrics

Revision ID: c9d1e2f3a4b5
Revises: b1d4e7f20c93
Create Date: 2026-04-18 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d1e2f3a4b5"
down_revision: Union[str, None] = "b1d4e7f20c93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

alertconditiontype_enum = postgresql.ENUM(
    "reputation_below",
    "spending_approaching",
    "error_rate_above",
    "anomalous_volume",
    "policy_violation_spike",
    "self_dealing_ratio_above",
    "principal_cluster_expansion",
    name="alertconditiontype",
    create_type=False,
)


def upgrade() -> None:
    # gateway_agents is a model table with no earlier migration. A fresh
    # database has nothing to alter. Create it, including principal_id.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("gateway_agents"):
        op.create_table(
            "gateway_agents",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("exchange_account_id", sa.String(36), nullable=False),
            sa.Column("bot_name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("skills", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("exchange_claim_id", sa.String(36), nullable=True),
            sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column(
                "claimed_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("exchange_account_id"),
        )
    else:
        columns = {col["name"] for col in inspector.get_columns("gateway_agents")}
        if "principal_id" not in columns:
            op.add_column(
                "gateway_agents",
                sa.Column("principal_id", postgresql.UUID(as_uuid=True), nullable=True),
            )

    # Add diversity metrics to reputation_snapshots
    op.add_column(
        "reputation_snapshots",
        sa.Column("self_dealing_ratio_90d", sa.Float(), nullable=True),
    )
    op.add_column(
        "reputation_snapshots",
        sa.Column("counterparty_hhi", sa.Float(), nullable=True),
    )

    # Extend alert_condition_type enum with new self-dealing alert types.
    # PostgreSQL requires ALTER TYPE outside of a transaction for enum additions.
    op.execute(
        "ALTER TYPE alertconditiontype ADD VALUE IF NOT EXISTS 'self_dealing_ratio_above'"
    )
    op.execute(
        "ALTER TYPE alertconditiontype ADD VALUE IF NOT EXISTS 'principal_cluster_expansion'"
    )


def downgrade() -> None:
    op.drop_column("gateway_agents", "principal_id")
    op.drop_column("reputation_snapshots", "self_dealing_ratio_90d")
    op.drop_column("reputation_snapshots", "counterparty_hhi")
    # Note: PostgreSQL does not support removing enum values — downgrade leaves the enum values.
