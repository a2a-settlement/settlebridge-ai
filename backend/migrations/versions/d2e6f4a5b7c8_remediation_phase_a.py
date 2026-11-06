"""escrow holds, lease trust claims, repo auto-dispatch/auto-merge flags, alert dispatches

Revision ID: d2e6f4a5b7c8
Revises: c9d5e1f3a4b6
Create Date: 2026-11-06 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d2e6f4a5b7c8"
down_revision: Union[str, None] = "c9d5e1f3a4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("github_leases", sa.Column("hold_escrow_id", sa.String(255), nullable=True))
    op.add_column("github_leases", sa.Column("claim_kya", sa.Integer(), nullable=True))
    op.add_column("github_leases", sa.Column("claim_reputation", sa.Float(), nullable=True))
    op.add_column(
        "github_repo_links",
        sa.Column("auto_dispatch_alerts", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "github_repo_links",
        sa.Column("auto_merge_acceptable", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "github_alert_dispatches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("repo_full_name", sa.String(255), nullable=False),
        sa.Column("alert_kind", sa.String(32), nullable=False),
        sa.Column("alert_number", sa.Integer(), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=True),
        sa.Column("amount_ate", sa.BigInteger(), nullable=False),
        sa.Column(
            "policy_overrides",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("alert_state", sa.String(32), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "repo_full_name", "alert_kind", "alert_number", name="uq_github_alert_dispatch"
        ),
    )
    op.create_index(
        "ix_github_alert_dispatches_repo_issue",
        "github_alert_dispatches",
        ["repo_full_name", "issue_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_github_alert_dispatches_repo_issue", table_name="github_alert_dispatches")
    op.drop_table("github_alert_dispatches")
    op.drop_column("github_repo_links", "auto_merge_acceptable")
    op.drop_column("github_repo_links", "auto_dispatch_alerts")
    op.drop_column("github_leases", "claim_reputation")
    op.drop_column("github_leases", "claim_kya")
    op.drop_column("github_leases", "hold_escrow_id")
