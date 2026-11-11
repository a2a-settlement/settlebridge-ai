"""fix verifier tables: verifications, warranties, quotas, bypass submissions

Revision ID: d2e5f7a9c1b3
Revises: c8f1a2e4b6d0
Create Date: 2026-11-11 19:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "d2e5f7a9c1b3"
down_revision: Union[str, None] = "c8f1a2e4b6d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid(name: str, *args, **kwargs) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), *args, **kwargs)


def _ts(name: str, *, nullable: bool = True, now: bool = False) -> sa.Column:
    kwargs = {"server_default": sa.func.now()} if now else {}
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, **kwargs)


def upgrade() -> None:
    op.create_table(
        "fix_verifications",
        _uuid("id", primary_key=True),
        _uuid("repo_link_id", sa.ForeignKey("github_repo_links.id"), nullable=True),
        sa.Column("installation_id", sa.BigInteger(), nullable=True),
        sa.Column("repo_full_name", sa.String(255), nullable=False),
        sa.Column("vuln_id", sa.String(64), nullable=False),
        sa.Column("alert_ref", sa.String(255), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("base_sha", sa.String(40), nullable=False),
        sa.Column("fix_sha", sa.String(40), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=True),
        sa.Column("inconclusive_reason", sa.String(48), nullable=True),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("cost_cents", sa.Integer(), nullable=False),
        sa.Column("receipt_id", sa.String(64), nullable=True),
        sa.Column("embargo_state", sa.String(16), nullable=False),
        sa.Column("solver_kind", sa.String(16), nullable=False),
        sa.Column("solver_principal_id", sa.String(255), nullable=True),
        _uuid("created_by_user_id", sa.ForeignKey("users.id"), nullable=True),
        _ts("created_at", now=True),
        _ts("completed_at"),
        sa.UniqueConstraint(
            "repo_full_name", "fix_sha", "vuln_id", name="uq_fix_verifications_repo_fix_vuln"
        ),
    )
    op.create_index("ix_fix_verifications_repo_link_id", "fix_verifications", ["repo_link_id"])
    op.create_index(
        "ix_fix_verifications_installation_id", "fix_verifications", ["installation_id"]
    )
    op.create_index("ix_fix_verifications_receipt_id", "fix_verifications", ["receipt_id"])
    op.create_index(
        "ix_fix_verifications_repo_link_created",
        "fix_verifications",
        ["repo_link_id", "created_at"],
    )

    op.create_table(
        "fix_warranties",
        _uuid("id", primary_key=True),
        _uuid("binding_id", sa.ForeignKey("github_bounty_bindings.id"), nullable=False),
        _uuid("verification_id", sa.ForeignKey("fix_verifications.id"), nullable=False),
        sa.Column("holdback_cents", sa.Integer(), nullable=False),
        _ts("window_ends_at", nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("trigger_kind", sa.String(32), nullable=True),
        sa.Column("trigger_evidence", postgresql.JSONB(), nullable=False),
        _ts("review_due_at"),
        sa.Column("review_decision", sa.String(16), nullable=True),
        _uuid("reviewed_by_user_id", sa.ForeignKey("users.id"), nullable=True),
        _ts("created_at", now=True),
        _ts("resolved_at"),
    )
    op.create_index("ix_fix_warranties_binding_id", "fix_warranties", ["binding_id"])
    op.create_index("ix_fix_warranties_verification_id", "fix_warranties", ["verification_id"])
    op.create_index("ix_fix_warranties_state_window", "fix_warranties", ["state", "window_ends_at"])

    op.create_table(
        "verifier_quotas",
        _uuid("id", primary_key=True),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("limit", sa.Integer(), nullable=False),
        sa.Column("spend_cents", sa.Integer(), nullable=False),
        _ts("updated_at", now=True),
        sa.UniqueConstraint(
            "installation_id", "period", name="uq_verifier_quotas_installation_period"
        ),
    )

    op.create_table(
        "verifier_bypass_submissions",
        _uuid("id", primary_key=True),
        _uuid("verification_id", sa.ForeignKey("fix_verifications.id"), nullable=False),
        _uuid("submitter_user_id", sa.ForeignKey("users.id"), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("reproducer", postgresql.JSONB(), nullable=False),
        sa.Column("reward_cents", sa.Integer(), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        _ts("created_at", now=True),
        _ts("decided_at"),
    )
    op.create_index(
        "ix_verifier_bypass_submissions_verification_id",
        "verifier_bypass_submissions",
        ["verification_id"],
    )
    op.create_index(
        "ix_verifier_bypass_submissions_submitter_user_id",
        "verifier_bypass_submissions",
        ["submitter_user_id"],
    )


def downgrade() -> None:
    op.drop_table("verifier_bypass_submissions")
    op.drop_table("verifier_quotas")
    op.drop_table("fix_warranties")
    op.drop_table("fix_verifications")
