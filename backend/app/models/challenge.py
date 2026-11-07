"""Challenge engine records: frozen contracts, attempts, structured claims, verdicts, findings.

Contracts, artifacts, and verdicts are insert-only. A verdict always applies to the
stated property, environment, and budget of its frozen contract.
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


class ChallengeMode(str, enum.Enum):
    OPEN_PR = "open_pr"
    RETROSPECTIVE = "retrospective"


class ChallengeTaskState(str, enum.Enum):
    DRAFT = "draft"
    FROZEN = "frozen"
    BASELINING = "baselining"
    OPEN = "open"
    ATTEMPTING = "attempting"
    VALIDATING = "validating"
    ADJUDICATING = "adjudicating"
    CLOSED = "closed"
    INCONCLUSIVE = "inconclusive"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATES = frozenset(
    {
        ChallengeTaskState.CLOSED.value,
        ChallengeTaskState.INCONCLUSIVE.value,
        ChallengeTaskState.CANCELLED.value,
    }
)

# from-state -> allowed to-states. The orchestrator rejects every other move.
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"frozen", "cancelled"}),
    "frozen": frozenset({"baselining", "cancelled"}),
    "baselining": frozenset({"open", "inconclusive", "cancelled"}),
    "open": frozenset({"attempting", "closed", "cancelled"}),
    "attempting": frozenset({"validating", "open", "inconclusive", "cancelled"}),
    "validating": frozenset({"adjudicating", "closed", "inconclusive", "open", "cancelled"}),
    "adjudicating": frozenset({"closed", "inconclusive", "cancelled"}),
    "closed": frozenset(),
    "inconclusive": frozenset(),
    "cancelled": frozenset(),
}


class ParticipantRole(str, enum.Enum):
    AUTHOR = "author"
    SOLVER = "solver"
    CHALLENGER = "challenger"
    VERIFIER = "verifier"
    ADJUDICATOR = "adjudicator"


class FailureClass(str, enum.Enum):
    AUTHORIZATION_CONFIDENTIALITY = "authorization_confidentiality"
    INTEGRITY = "integrity"
    INJECTION_EXECUTION = "injection_execution"
    DENIAL_OF_SERVICE = "denial_of_service"
    REGRESSION = "regression"


class OracleSource(str, enum.Enum):
    PROPERTY_CLAUSE = "property_clause"
    DOCUMENTED_RULE = "documented_rule"
    EXISTING_TEST = "existing_test"
    ISSUE_TEXT = "issue_text"
    CUSTOMER_RULE = "customer_rule"


class AttemptState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUBMITTED = "submitted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ClaimState(str, enum.Enum):
    SUBMITTED = "submitted"
    REJECTED_PRECHECK = "rejected_precheck"
    TAMPER_REJECTED = "tamper_rejected"
    VALIDATING = "validating"
    ADJUDICATING = "adjudicating"
    DECIDED = "decided"


CLAIM_TRANSITIONS: dict[str, frozenset[str]] = {
    "submitted": frozenset({"rejected_precheck", "tamper_rejected", "validating"}),
    "validating": frozenset({"adjudicating", "decided"}),
    "adjudicating": frozenset({"decided"}),
    "rejected_precheck": frozenset(),
    "tamper_rejected": frozenset(),
    "decided": frozenset(),
}


class VerdictKind(str, enum.Enum):
    CONFIRMED = "confirmed"
    NO_FAILURE_FOUND = "no_failure_found"
    INCONCLUSIVE = "inconclusive"
    INVALID_OR_DUPLICATE = "invalid_or_duplicate"


class VerdictScope(str, enum.Enum):
    CLAIM = "claim"
    TASK = "task"


class DecidedBy(str, enum.Enum):
    MACHINE = "machine"
    ADJUDICATOR = "adjudicator"


class ArtifactKind(str, enum.Enum):
    SOURCE_TARBALL = "source_tarball"
    HISTORY_BUNDLE = "history_bundle"
    WHEELHOUSE_MANIFEST = "wheelhouse_manifest"
    CHALLENGER_TEST = "challenger_test"
    OBSERVER_OUTPUT = "observer_output"
    CONTROL_OUTPUT = "control_output"
    RUN_LOG = "run_log"
    REPORT = "report"


class FindingKind(str, enum.Enum):
    CHALLENGE_GAP = "challenge_gap"
    HISTORICAL_GAP = "historical_gap"


class FindingStatus(str, enum.Enum):
    NEEDS_DECISION = "needs_decision"
    CONFIRMED = "confirmed"
    INCONCLUSIVE = "inconclusive"
    DISMISSED = "dismissed"
    SUPERSEDED = "superseded"


class ChallengeTask(Base):
    __tablename__ = "challenge_tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_challenge_tasks_idempotency_key"),
        Index("ix_challenge_tasks_repo_target", "repo_full_name", "target_sha"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    binding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("github_bounty_bindings.id"), nullable=True
    )
    base_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    target_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    property_text: Mapped[str] = mapped_column(Text, nullable=False)
    # List of FailureClass values funded by this task.
    failure_classes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # {"python": "3.11", "install": "...", "test_command": "..."}
    environment: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    budget_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ChallengeTaskState.DRAFT.value, index=True
    )
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChallengeContract(Base):
    """Insert-only frozen terms. ``content_hash`` is sha256 of the canonical JSON."""

    __tablename__ = "challenge_contracts"
    __table_args__ = (UniqueConstraint("task_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_tasks.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Canonical body: property, failure_classes, oracle_sources, observers, environment,
    # budget_cents, deadline_at, base_sha, target_sha, fixtures_digest.
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChallengeParticipant(Base):
    __tablename__ = "challenge_participants"
    __table_args__ = (UniqueConstraint("task_id", "role", "operator_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_tasks.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # Stable operator identity: a user id, or "platform:<worker-name>" for platform workers.
    operator_id: Mapped[str] = mapped_column(String(255), nullable=False)
    platform_operated: Mapped[bool] = mapped_column(default=False, nullable=False)
    conflict_declaration: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Artifact ids this participant was shown.
    artifacts_seen: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChallengeAttempt(Base):
    __tablename__ = "challenge_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_tasks.id"), nullable=False, index=True
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_participants.id"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_contracts.id"), nullable=False
    )
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AttemptState.QUEUED.value
    )
    # spend_reservations.id that funds this attempt; usage rows link back to it.
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChallengeClaim(Base):
    """Structured claim (plan section 4). Free text alone is never a claim."""

    __tablename__ = "challenge_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_tasks.id"), nullable=False, index=True
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_attempts.id"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_contracts.id"), nullable=False
    )
    failure_class: Mapped[str] = mapped_column(String(32), nullable=False)
    # {"precondition": str, "setup": str, "action": str, "test_path": "challenge_tests/..."}
    scenario: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # {"source": OracleSource, "reference": str, "quote": str}
    oracle_citation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expected: Mapped[str] = mapped_column(Text, nullable=False)
    observed: Mapped[str] = mapped_column(Text, nullable=False)
    claimed_impact: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ClaimState.SUBMITTED.value
    )
    # sha256 over the canonical claim body; duplicates share it.
    claim_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChallengeArtifact(Base):
    """Insert-only content-addressed artifact. Blobs live on disk under the artifact root."""

    __tablename__ = "challenge_artifacts"
    __table_args__ = (UniqueConstraint("task_id", "content_hash", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_tasks.id"), nullable=False, index=True
    )
    tenant_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChallengeVerdict(Base):
    """Append-only. The latest row per (task, claim) is current; earlier rows are history."""

    __tablename__ = "challenge_verdicts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_tasks.id"), nullable=False, index=True
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_claims.id"), nullable=True
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_contracts.id"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(8), nullable=False)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(16), nullable=False)
    adjudicator_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    # Short machine or adjudicator reason, e.g. "positive control failed".
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # ValidationEvidence (schemas/challenge.py) as JSON.
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Finding(Base):
    """Customer-facing result shared by M2 (challenge_gap) and M3 (historical_gap)."""

    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("tenant_user_id", "dedupe_key", name="uq_findings_tenant_dedupe"),
        Index("ix_findings_repo_status", "repo_full_name", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_tasks.id"), nullable=True
    )
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_claims.id"), nullable=True
    )
    verdict_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("challenge_verdicts.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=FindingStatus.NEEDS_DECISION.value
    )
    failure_class: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Only set when cited from an advisory, e.g. {"level": "high", "source": "GHSA-..."}.
    advisory_severity: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    target_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    reopen_evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


OPEN_FINDING_STATUSES = (FindingStatus.NEEDS_DECISION.value,)
