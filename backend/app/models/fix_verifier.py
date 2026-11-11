"""Fix verifier records: verifications, warranties, quotas and verifier-bypass reports.

A verification row is the result cache: one row per (repo, fix_sha, vuln_id). Verdict
strings follow settlebridge_verifier.interfaces (verified, incomplete_fix_confirmed,
inconclusive). Warranty state only moves forward; ``forfeited`` needs a
security-motivated revert or an overlapping follow-up advisory.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FixVerdict(str, enum.Enum):
    VERIFIED = "verified"
    INCOMPLETE_FIX_CONFIRMED = "incomplete_fix_confirmed"
    INCONCLUSIVE = "inconclusive"


class FixVerificationState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class SolverKind(str, enum.Enum):
    HOUSE = "house"
    THIRD_PARTY = "third_party"
    UNKNOWN = "unknown"


class WarrantyState(str, enum.Enum):
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"
    RELEASED = "released"
    FORFEITED = "forfeited"


class BypassState(str, enum.Enum):
    SUBMITTED = "submitted"
    TRIAGED = "triaged"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class FixVerification(Base):
    __tablename__ = "fix_verifications"
    __table_args__ = (
        UniqueConstraint(
            "repo_full_name", "fix_sha", "vuln_id", name="uq_fix_verifications_repo_fix_vuln"
        ),
        Index("ix_fix_verifications_repo_link_created", "repo_link_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_repo_links.id"), nullable=True, index=True
    )
    installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    vuln_id: Mapped[str] = mapped_column(String(64), nullable=False)
    alert_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    fix_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=FixVerificationState.QUEUED.value
    )
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    inconclusive_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    receipt_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    embargo_state: Mapped[str] = mapped_column(String(16), nullable=False, default="private")
    solver_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SolverKind.UNKNOWN.value
    )
    solver_principal_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FixWarranty(Base):
    __tablename__ = "fix_warranties"
    __table_args__ = (Index("ix_fix_warranties_state_window", "state", "window_ends_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_bounty_bindings.id"), nullable=False, index=True
    )
    verification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fix_verifications.id"), nullable=False, index=True
    )
    holdback_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    window_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=WarrantyState.ACTIVE.value
    )
    trigger_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trigger_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VerifierQuota(Base):
    __tablename__ = "verifier_quotas"
    __table_args__ = (
        UniqueConstraint("installation_id", "period", name="uq_verifier_quotas_installation_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    limit: Mapped[int] = mapped_column(Integer, nullable=False)
    spend_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VerifierBypassSubmission(Base):
    __tablename__ = "verifier_bypass_submissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    verification_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fix_verifications.id"), nullable=False, index=True
    )
    submitter_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=BypassState.SUBMITTED.value
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    reproducer: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reward_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
