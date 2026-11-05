"""add github identities and bounty leases

Revision ID: b8c4d0e2f3a5
Revises: a7b3c9d1e2f4
Create Date: 2026-11-05 10:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b8c4d0e2f3a5"
down_revision: Union[str, None] = "a7b3c9d1e2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "github_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False, unique=True
        ),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("login", sa.String(length=255), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_table(
        "github_leases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "binding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_bounty_bindings.id"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "spec_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_acceptance_specs.id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="work"),
        sa.Column("work_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hard_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hold_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("otel_trace_id", sa.String(length=64), nullable=True),
        sa.Column("release_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
    )
    op.create_index("ix_github_leases_binding_id", "github_leases", ["binding_id"])
    op.create_index("ix_github_leases_agent_id", "github_leases", ["agent_id"])
    op.create_index(
        "uq_github_leases_active_binding",
        "github_leases",
        ["binding_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('work', 'submitted')"),
    )
    op.create_index(
        "uq_github_bounty_bindings_lower_repo_issue",
        "github_bounty_bindings",
        [sa.text("lower(repo_full_name)"), "issue_number"],
        unique=True,
    )
    op.create_index(
        "uq_github_bounty_bindings_lower_repo_pr",
        "github_bounty_bindings",
        [sa.text("lower(repo_full_name)"), "pr_number"],
        unique=True,
        postgresql_where=sa.text("pr_number IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_github_bounty_bindings_lower_repo_pr",
        table_name="github_bounty_bindings",
    )
    op.drop_index(
        "uq_github_bounty_bindings_lower_repo_issue",
        table_name="github_bounty_bindings",
    )
    op.drop_index("uq_github_leases_active_binding", table_name="github_leases")
    op.drop_index("ix_github_leases_agent_id", table_name="github_leases")
    op.drop_index("ix_github_leases_binding_id", table_name="github_leases")
    op.drop_table("github_leases")
    op.drop_table("github_identities")
