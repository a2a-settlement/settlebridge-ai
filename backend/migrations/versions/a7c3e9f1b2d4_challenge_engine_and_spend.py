"""challenge engine, findings, spend ledger, job dedupe key

Revision ID: a7c3e9f1b2d4
Revises: f6a1c8d24e90
Create Date: 2026-11-07 11:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a7c3e9f1b2d4"
down_revision: Union[str, None] = "f6a1c8d24e90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_JSONB = postgresql.JSONB()


def _ts(name: str, nullable: bool = True, server_now: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False if server_now else nullable,
        server_default=sa.func.now() if server_now else None,
    )


def upgrade() -> None:
    op.add_column("github_jobs", sa.Column("dedupe_key", sa.String(255), nullable=True))
    op.create_index(
        "uq_github_jobs_open_dedupe_key",
        "github_jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL AND status IN ('pending', 'running')"),
    )

    op.add_column(
        "github_leases", sa.Column("claim_idempotency_key", sa.String(128), nullable=True)
    )
    op.add_column(
        "github_leases", sa.Column("submit_idempotency_key", sa.String(128), nullable=True)
    )
    op.create_index(
        "uq_github_leases_agent_claim_key",
        "github_leases",
        ["agent_id", "claim_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("claim_idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "challenge_tasks",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("owner_user_id", _UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("repo_full_name", sa.String(255), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("binding_id", _UUID, sa.ForeignKey("github_bounty_bindings.id"), nullable=True),
        sa.Column("base_sha", sa.String(64), nullable=False),
        sa.Column("target_sha", sa.String(64), nullable=False),
        sa.Column("property_text", sa.Text(), nullable=False),
        sa.Column("failure_classes", _JSONB, nullable=False),
        sa.Column("environment", _JSONB, nullable=False),
        sa.Column("budget_cents", sa.Integer(), nullable=False),
        _ts("deadline_at", nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("current_contract_id", _UUID, nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        _ts("created_at", server_now=True),
        _ts("updated_at", server_now=True),
        sa.UniqueConstraint("idempotency_key", name="uq_challenge_tasks_idempotency_key"),
    )
    op.create_index("ix_challenge_tasks_owner_user_id", "challenge_tasks", ["owner_user_id"])
    op.create_index("ix_challenge_tasks_state", "challenge_tasks", ["state"])
    op.create_index(
        "ix_challenge_tasks_repo_target", "challenge_tasks", ["repo_full_name", "target_sha"]
    )

    op.create_table(
        "challenge_contracts",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("task_id", _UUID, sa.ForeignKey("challenge_tasks.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", _JSONB, nullable=False),
        _ts("created_at", server_now=True),
        sa.UniqueConstraint("task_id", "version"),
    )
    op.create_index("ix_challenge_contracts_task_id", "challenge_contracts", ["task_id"])

    op.create_table(
        "challenge_participants",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("task_id", _UUID, sa.ForeignKey("challenge_tasks.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("operator_id", sa.String(255), nullable=False),
        sa.Column("platform_operated", sa.Boolean(), nullable=False),
        sa.Column("conflict_declaration", _JSONB, nullable=False),
        sa.Column("artifacts_seen", _JSONB, nullable=False),
        _ts("created_at", server_now=True),
        sa.UniqueConstraint("task_id", "role", "operator_id"),
    )
    op.create_index("ix_challenge_participants_task_id", "challenge_participants", ["task_id"])

    op.create_table(
        "challenge_attempts",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("task_id", _UUID, sa.ForeignKey("challenge_tasks.id"), nullable=False),
        sa.Column(
            "participant_id", _UUID, sa.ForeignKey("challenge_participants.id"), nullable=False
        ),
        sa.Column("contract_id", _UUID, sa.ForeignKey("challenge_contracts.id"), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("reservation_id", _UUID, nullable=True),
        _ts("started_at"),
        _ts("finished_at"),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("created_at", server_now=True),
    )
    op.create_index("ix_challenge_attempts_task_id", "challenge_attempts", ["task_id"])

    op.create_table(
        "challenge_claims",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("task_id", _UUID, sa.ForeignKey("challenge_tasks.id"), nullable=False),
        sa.Column("attempt_id", _UUID, sa.ForeignKey("challenge_attempts.id"), nullable=False),
        sa.Column("contract_id", _UUID, sa.ForeignKey("challenge_contracts.id"), nullable=False),
        sa.Column("failure_class", sa.String(32), nullable=False),
        sa.Column("scenario", _JSONB, nullable=False),
        sa.Column("oracle_citation", _JSONB, nullable=False),
        sa.Column("expected", sa.Text(), nullable=False),
        sa.Column("observed", sa.Text(), nullable=False),
        sa.Column("claimed_impact", sa.Text(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("claim_hash", sa.String(64), nullable=False),
        _ts("submitted_at", server_now=True),
    )
    op.create_index("ix_challenge_claims_task_id", "challenge_claims", ["task_id"])
    op.create_index("ix_challenge_claims_claim_hash", "challenge_claims", ["claim_hash"])

    op.create_table(
        "challenge_artifacts",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("task_id", _UUID, sa.ForeignKey("challenge_tasks.id"), nullable=False),
        sa.Column("tenant_user_id", _UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.String(512), nullable=False),
        sa.Column("meta", _JSONB, nullable=False),
        _ts("created_at", server_now=True),
        sa.UniqueConstraint("task_id", "content_hash", "kind"),
    )
    op.create_index("ix_challenge_artifacts_task_id", "challenge_artifacts", ["task_id"])

    op.create_table(
        "challenge_verdicts",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("task_id", _UUID, sa.ForeignKey("challenge_tasks.id"), nullable=False),
        sa.Column("claim_id", _UUID, sa.ForeignKey("challenge_claims.id"), nullable=True),
        sa.Column("contract_id", _UUID, sa.ForeignKey("challenge_contracts.id"), nullable=False),
        sa.Column("scope", sa.String(8), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("decided_by", sa.String(16), nullable=False),
        sa.Column("adjudicator_user_id", _UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", _JSONB, nullable=False),
        _ts("created_at", server_now=True),
    )
    op.create_index("ix_challenge_verdicts_task_id", "challenge_verdicts", ["task_id"])

    op.create_table(
        "findings",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("tenant_user_id", _UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("repo_full_name", sa.String(255), nullable=False),
        sa.Column("task_id", _UUID, sa.ForeignKey("challenge_tasks.id"), nullable=True),
        sa.Column("claim_id", _UUID, sa.ForeignKey("challenge_claims.id"), nullable=True),
        sa.Column("verdict_id", _UUID, sa.ForeignKey("challenge_verdicts.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_class", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("advisory_severity", _JSONB, nullable=True),
        sa.Column("target_sha", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=False),
        sa.Column("reopen_evidence_hash", sa.String(64), nullable=True),
        _ts("created_at", server_now=True),
        _ts("updated_at", server_now=True),
        sa.UniqueConstraint("tenant_user_id", "dedupe_key", name="uq_findings_tenant_dedupe"),
    )
    op.create_index("ix_findings_tenant_user_id", "findings", ["tenant_user_id"])
    op.create_index("ix_findings_repo_status", "findings", ["repo_full_name", "status"])

    op.create_table(
        "account_budgets",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("period_cap_cents", sa.BigInteger(), nullable=False),
        sa.Column("per_job_cap_cents", sa.BigInteger(), nullable=False),
        sa.Column("max_concurrent_jobs", sa.Integer(), nullable=False),
        _ts("period_start", nullable=False),
        sa.Column("reserved_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("spent_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        _ts("paused_at"),
        _ts("created_at", server_now=True),
        _ts("updated_at", server_now=True),
    )

    op.create_table(
        "spend_reservations",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("budget_id", _UUID, sa.ForeignKey("account_budgets.id"), nullable=False),
        sa.Column("subject", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("reserved_cents", sa.BigInteger(), nullable=False),
        sa.Column("used_cents", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(16), nullable=False),
        _ts("expires_at", nullable=False),
        _ts("created_at", server_now=True),
        _ts("updated_at", server_now=True),
        sa.UniqueConstraint("idempotency_key", name="uq_spend_reservations_idempotency_key"),
    )
    op.create_index("ix_spend_reservations_budget_id", "spend_reservations", ["budget_id"])
    op.create_index("ix_spend_reservations_status", "spend_reservations", ["status"])

    op.create_table(
        "execution_usage",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("reservation_id", _UUID, sa.ForeignKey("spend_reservations.id"), nullable=False),
        sa.Column("execution_id", sa.String(128), nullable=False),
        sa.Column("cause", sa.String(32), nullable=False),
        sa.Column("charged", sa.Boolean(), nullable=False),
        sa.Column("cpu_seconds", sa.Integer(), nullable=False),
        sa.Column("wall_seconds", sa.Integer(), nullable=False),
        sa.Column("model_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_cents", sa.BigInteger(), nullable=False),
        _ts("created_at", server_now=True),
        sa.UniqueConstraint("execution_id", name="uq_execution_usage_execution_id"),
    )
    op.create_index("ix_execution_usage_reservation_id", "execution_usage", ["reservation_id"])


def downgrade() -> None:
    op.drop_table("execution_usage")
    op.drop_table("spend_reservations")
    op.drop_table("account_budgets")
    op.drop_table("findings")
    op.drop_table("challenge_verdicts")
    op.drop_table("challenge_artifacts")
    op.drop_table("challenge_claims")
    op.drop_table("challenge_attempts")
    op.drop_table("challenge_participants")
    op.drop_table("challenge_contracts")
    op.drop_table("challenge_tasks")
    op.drop_index("uq_github_leases_agent_claim_key", table_name="github_leases")
    op.drop_column("github_leases", "submit_idempotency_key")
    op.drop_column("github_leases", "claim_idempotency_key")
    op.drop_index("uq_github_jobs_open_dedupe_key", table_name="github_jobs")
    op.drop_column("github_jobs", "dedupe_key")
