"""GitHub App bounty bridge: installations, frozen acceptance specs, durable jobs."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BindingState(str, enum.Enum):
    ADVERTISED = "advertised"
    EVALUATING = "evaluating"
    NEEDS_REVIEW = "needs_review"
    AWAITING_MERGE = "awaiting_merge"
    SETTLEMENT_PENDING = "settlement_pending"
    PAID = "paid"
    CANCELLED = "cancelled"


class LedgerStatus(str, enum.Enum):
    ADVERTISED = "advertised"
    RELEASED = "released"
    SETTLED = "settled"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    DEAD = "dead"


class JobKind(str, enum.Enum):
    INSTALLATION = "installation"
    ISSUE_LABELED = "issue_labeled"
    ISSUE_CHANGED = "issue_changed"
    ISSUE_CLOSED = "issue_closed"
    PR_UPDATED = "pr_updated"
    PR_CLOSED = "pr_closed"
    CHECKS_COMPLETED = "checks_completed"
    REVIEW_CLEAR_COMMENT = "review_clear_comment"
    ADVANCE_SETTLEMENT = "advance_settlement"
    SECURITY_ALERT = "security_alert"
    REFUND_HOLD = "refund_hold"


class SettlementState(str, enum.Enum):
    PENDING = "pending"
    ESCROW_CREATED = "escrow_created"
    DELIVERED = "delivered"
    RELEASED = "released"
    FAILED = "failed"


class LeaseStatus(str, enum.Enum):
    WORK = "work"
    SUBMITTED = "submitted"
    RELEASED = "released"
    EXPIRED = "expired"
    SETTLED = "settled"


ACTIVE_LEASE_STATUSES = (LeaseStatus.WORK.value, LeaseStatus.SUBMITTED.value)


class GitHubIdentity(Base):
    """A GitHub account verified through the GitHub App's user OAuth flow."""

    __tablename__ = "github_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False
    )
    github_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    login: Mapped[str] = mapped_column(String(255), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GitHubLease(Base):
    """Lease of record for payout attribution. ``spec_id`` pins the acceptance terms."""

    __tablename__ = "github_leases"
    __table_args__ = (
        Index(
            "uq_github_leases_active_binding",
            "binding_id",
            unique=True,
            postgresql_where=text("status IN ('work', 'submitted')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_bounty_bindings.id"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    github_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    spec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_acceptance_specs.id"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=LeaseStatus.WORK.value
    )
    work_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hard_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    otel_trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Escrow created from the funder at claim time (GITHUB_LOCK_ESCROW_ON_LEASE_CLAIM).
    hold_escrow_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Trust claims seen at claim, refreshed at submit; used by auto-merge.
    claim_kya: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claim_reputation: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GitHubInstallation(Base):
    __tablename__ = "github_installations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    installation_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GitHubRepoLink(Base):
    """A repository whose bounties are funded by ``funder_user_id``.

    ``default_policy`` has the shape of the mediator's PatchPolicy.
    ``required_checks`` is a list of ``{"name": str, "app_id": int}``.
    """

    __tablename__ = "github_repo_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    installation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("github_installations.installation_id"), nullable=False
    )
    repo_full_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    funder_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(default=False)
    default_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    required_checks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    onboarded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_dispatch_alerts: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )
    auto_merge_acceptable: Mapped[bool] = mapped_column(
        default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertKind(str, enum.Enum):
    DEPENDABOT = "dependabot"
    CODE_SCANNING = "code_scanning"


class GitHubAlertDispatch(Base):
    """A security alert turned into a bounty issue.

    ``policy_overrides`` merges into the repo's ``default_policy`` for that issue's specs.
    ``alert_state`` mirrors GitHub's alert state once settlement starts polling it.
    """

    __tablename__ = "github_alert_dispatches"
    __table_args__ = (
        UniqueConstraint(
            "repo_full_name", "alert_kind", "alert_number", name="uq_github_alert_dispatch"
        ),
        Index("ix_github_alert_dispatches_repo_issue", "repo_full_name", "issue_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    alert_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    alert_number: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_ate: Mapped[int] = mapped_column(BigInteger, nullable=False)
    policy_overrides: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    alert_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GitHubBountyBinding(Base):
    __tablename__ = "github_bounty_bindings"
    __table_args__ = (
        UniqueConstraint("repo_full_name", "issue_number"),
        Index(
            "uq_github_bounty_bindings_lower_repo_issue",
            func.lower(text("repo_full_name")),
            text("issue_number"),
            unique=True,
        ),
        Index(
            "uq_github_bounty_bindings_lower_repo_pr",
            func.lower(text("repo_full_name")),
            text("pr_number"),
            unique=True,
            postgresql_where=text("pr_number IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bounty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id"), nullable=False
    )
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_number: Mapped[int] = mapped_column(Integer, nullable=False)
    current_spec_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evaluated_head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evaluated_spec_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=BindingState.ADVERTISED.value
    )
    lease_holder_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_assessment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    review_cleared_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AcceptanceSpec(Base):
    """Immutable acceptance terms. Edits create a new version; rows are never updated."""

    __tablename__ = "github_acceptance_specs"
    __table_args__ = (UniqueConstraint("binding_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_bounty_bindings.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    test_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_checks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    payment_trigger: Mapped[str] = mapped_column(
        String(64), nullable=False, default="merge_with_checks"
    )
    amount_ate: Mapped[int] = mapped_column(Integer, nullable=False)
    fee_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    required_kya: Mapped[int] = mapped_column(Integer, nullable=False)
    required_ema: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FunderLedgerEntry(Base):
    """Pilot-only advertised-amount ledger. Not a reservation of funds."""

    __tablename__ = "github_funder_ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    funder_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_bounty_bindings.id"), unique=True, nullable=False
    )
    amount_ate: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=LedgerStatus.ADVERTISED.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GitHubWebhookDelivery(Base):
    __tablename__ = "github_webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GitHubJob(Base):
    __tablename__ = "github_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    delivery_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("github_webhook_deliveries.delivery_id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JobStatus.PENDING.value, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GitHubSettlement(Base):
    __tablename__ = "github_settlements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_bounty_bindings.id"), unique=True, nullable=False
    )
    spec_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_acceptance_specs.id"), nullable=False
    )
    solver_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SettlementState.PENDING.value
    )
    escrow_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_prefix: Mapped[str] = mapped_column(String(128), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
