"""add github bounty bridge tables

Revision ID: a7b3c9d1e2f4
Revises: c7e4f5a61b20
Create Date: 2026-11-04 22:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a7b3c9d1e2f4"
down_revision: Union[str, None] = "c7e4f5a61b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "github_installations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("installation_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("account_login", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "github_repo_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "installation_id",
            sa.BigInteger(),
            sa.ForeignKey("github_installations.installation_id"),
            nullable=False,
        ),
        sa.Column("repo_full_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("funder_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_policy", _JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("required_checks", _JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_table(
        "github_bounty_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("bounty_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bounties.id"), nullable=False),
        sa.Column("repo_full_name", sa.String(length=255), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("current_spec_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("evaluated_head_sha", sa.String(length=64), nullable=True),
        sa.Column("evaluated_spec_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="advertised"),
        sa.Column("lease_holder_agent_id", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment_id", sa.BigInteger(), nullable=True),
        sa.Column("last_assessment", _JSONB, nullable=True),
        sa.Column("review_cleared_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("repo_full_name", "issue_number"),
    )
    op.create_table(
        "github_acceptance_specs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_bounty_bindings.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("test_command", sa.Text(), nullable=True),
        sa.Column("required_checks", _JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("policy", _JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payment_trigger", sa.String(length=64), nullable=False, server_default="merge_with_checks"),
        sa.Column("amount_ate", sa.Integer(), nullable=False),
        sa.Column("fee_bps", sa.Integer(), nullable=False),
        sa.Column("required_kya", sa.Integer(), nullable=False),
        sa.Column("required_ema", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.UniqueConstraint("binding_id", "version"),
    )
    op.create_table(
        "github_funder_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("funder_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_bounty_bindings.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("amount_ate", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="advertised"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index("ix_github_funder_ledger_funder_user_id", "github_funder_ledger", ["funder_user_id"])
    op.create_table(
        "github_webhook_deliveries",
        sa.Column("delivery_id", sa.String(length=64), primary_key=True),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_table(
        "github_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "delivery_id",
            sa.String(length=64),
            sa.ForeignKey("github_webhook_deliveries.delivery_id"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", _JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False, server_default=_NOW),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index("ix_github_jobs_status", "github_jobs", ["status"])
    op.create_table(
        "github_settlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_bounty_bindings.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "spec_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_acceptance_specs.id"),
            nullable=False,
        ),
        sa.Column("solver_agent_id", sa.String(length=255), nullable=True),
        sa.Column("head_sha", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("escrow_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_prefix", sa.String(length=128), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("github_settlements")
    op.drop_index("ix_github_jobs_status", table_name="github_jobs")
    op.drop_table("github_jobs")
    op.drop_table("github_webhook_deliveries")
    op.drop_index("ix_github_funder_ledger_funder_user_id", table_name="github_funder_ledger")
    op.drop_table("github_funder_ledger")
    op.drop_table("github_acceptance_specs")
    op.drop_table("github_bounty_bindings")
    op.drop_table("github_repo_links")
    op.drop_table("github_installations")
