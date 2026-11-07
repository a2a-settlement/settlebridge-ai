"""S0 frozen contracts for the challenge engine, task detail, and findings.

Only the coordinator edits this module. Workers import it; a worker that finds a
contract wrong stops and reports instead of changing it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Sha = str  # 40-hex git object id
Digest = str  # "sha256:<64 hex>"

FailureClassLiteral = Literal[
    "authorization_confidentiality",
    "integrity",
    "injection_execution",
    "denial_of_service",
    "regression",
]
OracleSourceLiteral = Literal[
    "property_clause", "documented_rule", "existing_test", "issue_text", "customer_rule"
]
VerdictLiteral = Literal["confirmed", "no_failure_found", "inconclusive", "invalid_or_duplicate"]
TaskStateLiteral = Literal[
    "draft",
    "frozen",
    "baselining",
    "open",
    "attempting",
    "validating",
    "adjudicating",
    "closed",
    "inconclusive",
    "cancelled",
]
FindingStatusLiteral = Literal[
    "needs_decision", "confirmed", "inconclusive", "dismissed", "superseded"
]
UsageCauseLiteral = Literal[
    "initial", "rerun", "control", "rederivation", "dependency_acquisition", "platform_retry"
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- runner protocol v1
# The backend writes RunnerJob as JSON, invokes `python -m challenge_runner run`, and
# reads RunnerResult. challenge-runner/challenge_runner/protocol.py mirrors these
# shapes with stdlib dataclasses; docs/cyber/runner-protocol.schema.json is the
# shared JSON Schema both sides test against.


class ResourceLimits(_Frozen):
    cpu_seconds: int = Field(gt=0, le=3600)
    memory_mb: int = Field(gt=0, le=8192)
    wall_seconds: int = Field(gt=0, le=3600)
    disk_mb: int = Field(gt=0, le=20480)
    max_processes: int = Field(default=256, gt=0, le=4096)


class FetchSpec(_Frozen):
    """Trusted fetcher input. The token is passed out of band, never in this JSON."""

    repo_full_name: str
    installation_id: int
    sha: Sha
    include_history: bool = False


class RunnerJob(_Frozen):
    protocol: Literal["challenge-runner/v1"] = "challenge-runner/v1"
    execution_id: str = Field(min_length=8, max_length=128)
    stage: Literal["dependencies", "execute"]
    task_id: str
    source_tarball: Digest
    # Stage "execute" only: the wheelhouse produced by stage "dependencies".
    wheelhouse_manifest: Digest | None = None
    # Stage "execute" only: frozen verifier fixtures and observers, mounted read-only.
    fixtures_digest: Digest | None = None
    # Stage "execute" only: challenger files, written under challenge_tests/ only.
    challenger_bundle: Digest | None = None
    python_version: str = "3.11"
    install_command: list[str] = Field(default_factory=list)
    test_command: list[str] = Field(default_factory=list)
    allowlist_hosts: list[str] = Field(
        default_factory=lambda: ["pypi.org", "files.pythonhosted.org"]
    )
    limits: ResourceLimits
    observers: list[str] = Field(default_factory=list)
    canaries_digest: Digest | None = None


class ObserverEvent(_Frozen):
    observer: str
    kind: Literal["canary_seen", "state_changed", "sentinel_triggered", "threshold_exceeded"]
    detail: str
    location: str


class RunnerResult(_Frozen):
    protocol: Literal["challenge-runner/v1"] = "challenge-runner/v1"
    execution_id: str
    stage: Literal["dependencies", "execute"]
    status: Literal["ok", "test_failed", "limit_exceeded", "sandbox_error", "cancelled"]
    exit_code: int | None = None
    cpu_seconds: int = 0
    wall_seconds: int = 0
    image_digest: Digest
    source_digest: Digest
    wheelhouse_manifest: Digest | None = None
    observer_events: list[ObserverEvent] = Field(default_factory=list)
    # Sanitized log artifact; raw logs never leave the runner.
    log_digest: Digest | None = None
    error: str | None = None


# ---------------------------------------------------------------- structured claim


class Scenario(_Frozen):
    precondition: str = Field(min_length=1, max_length=4000)
    setup: str = Field(min_length=1, max_length=4000)
    action: str = Field(min_length=1, max_length=4000)
    test_path: str = Field(pattern=r"^challenge_tests/[A-Za-z0-9_./-]+\.py$")


class OracleCitation(_Frozen):
    source: OracleSourceLiteral
    reference: str = Field(min_length=1, max_length=500)
    quote: str = Field(min_length=1, max_length=2000)


class ClaimIn(_Frozen):
    failure_class: FailureClassLiteral
    scenario: Scenario
    oracle_citation: OracleCitation
    expected: str = Field(min_length=1, max_length=4000)
    observed: str = Field(min_length=1, max_length=4000)
    claimed_impact: str = Field(min_length=1, max_length=4000)


# ---------------------------------------------------------------- validation evidence


class ControlOutcome(_Frozen):
    positive_ok: bool
    negative_ok: bool
    detail: str = ""


class DifferentialOutcome(_Frozen):
    base_fired: bool | None
    target_fired: bool
    reference_fired: bool | None = None
    classification: Literal["same_class_alternate_path", "new_path", "regression", "not_applicable"]


class ValidationEvidence(_Frozen):
    """Stored on challenge_verdicts.evidence. Every check is recorded, pass or fail."""

    citation_valid: bool
    tamper_clean: bool
    observer_fired: bool
    observer_events: list[ObserverEvent] = Field(default_factory=list)
    controls: ControlOutcome | None = None
    differential: DifferentialOutcome | None = None
    rederivation_fired: bool | None = None
    reruns_reproduced: int = Field(default=0, ge=0, le=3)
    minimized_reproduced: bool | None = None
    execution_ids: list[str] = Field(default_factory=list)
    needs_adjudication: bool = False


class ValidationOutcome(_Frozen):
    verdict: VerdictLiteral | None  # None means "send to adjudication"
    reason: str
    evidence: ValidationEvidence


# ---------------------------------------------------------------- API


class ChallengeTaskCreate(_Frozen):
    repo_full_name: str
    pr_number: int | None = None
    base_sha: Sha
    target_sha: Sha
    property_text: str = Field(min_length=10, max_length=4000)
    failure_classes: list[FailureClassLiteral] = Field(min_length=1)
    budget_cents: int = Field(gt=0, le=100_000)
    deadline_at: datetime


class ParticipantOut(BaseModel):
    role: Literal["author", "solver", "challenger", "verifier", "adjudicator"]
    operator_label: str
    platform_operated: bool


class VerdictOut(BaseModel):
    claim_id: str | None
    verdict: VerdictLiteral
    decided_by: Literal["machine", "adjudicator"]
    reason: str
    created_at: datetime


class AdvisorySeverity(BaseModel):
    level: Literal["critical", "high", "medium", "low"]
    source: str


class FindingOut(BaseModel):
    id: str
    kind: Literal["challenge_gap", "historical_gap"]
    repo: str
    status: FindingStatusLiteral
    failure_class: FailureClassLiteral
    title: str
    # None renders as "Severity not rated".
    advisory_severity: AdvisorySeverity | None
    oracle_citation: OracleCitation | None
    target_sha: Sha
    evidence_url: str
    created_at: datetime


class ChallengeSummary(BaseModel):
    claims_submitted: int
    confirmed: int
    inconclusive: int
    invalid: int
    no_failure_found: bool


class ChallengeDetailOut(BaseModel):
    """GET /api/v1/challenges/{id}."""

    id: str
    repo: str
    pr_number: int | None
    base_sha: Sha
    target_sha: Sha
    state: TaskStateLiteral
    state_reason: str | None
    property_text: str
    participants: list[ParticipantOut]
    summary: ChallengeSummary
    verdicts: list[VerdictOut]
    findings: list[FindingOut]
    report_url: str | None
    scope_note: str = "Under the stated property, environment, and budget."


class ChecksResult(BaseModel):
    state: Literal["passed", "failed", "pending", "not_run"]
    head_sha: Sha | None


class CyberTaskDetailOut(BaseModel):
    """GET /api/v1/cyber/tasks/{bounty_id}: the list item plus progress inputs. Read-only."""

    bounty_id: str
    title: str
    repo: str
    issue_number: int
    issue_url: str
    pr_number: int | None
    pr_url: str | None
    source: Literal["dependabot", "code_scanning", "security_label"]
    reward_ate: int
    funding_state: Literal[
        "balance_checked", "funds_held", "payment_pending", "payment_review", "paid", "not_paid"
    ]
    work_state: Literal[
        "available",
        "claimed",
        "pr_submitted",
        "evaluating",
        "needs_maintainer_review",
        "awaiting_merge",
        "settling",
        "completed",
        "cancelled",
    ]
    payment_trigger: Literal["merge_with_checks", "merged_and_alert_fixed"]
    eligibility: dict
    created_at: str
    updated_at: str
    checks: ChecksResult
    # "acceptable" | "needs_review" | None, from binding.last_assessment.
    evaluation: Literal["acceptable", "needs_review"] | None
    work_expires_at: str | None
    # Filled after M2; null before.
    challenge_id: str | None = None
    challenge_verdict: VerdictLiteral | None = None
    open_findings: int = 0


class MyLeaseOut(BaseModel):
    """One item of GET /api/v1/github/leases/mine (P0-F1). Caller's own leases only."""

    lease_id: str
    bounty_id: str
    repo: str
    issue_number: int
    status: Literal["work", "submitted", "released", "expired", "settled"]
    work_expires_at: str
    pr_number: int | None
    evaluation: Literal["acceptable", "needs_review"] | None
    payout: Literal["paid", "pending", "not_paid"] | None
    amount_ate: int


class MyLeasesOut(BaseModel):
    items: list[MyLeaseOut]


# ---------------------------------------------------------------- report


class ReportParticipant(BaseModel):
    role: str
    operator_label: str
    platform_operated: bool
    artifacts_seen: list[str]


class ReportClaim(BaseModel):
    claim_id: str
    failure_class: FailureClassLiteral
    oracle_citation: OracleCitation
    verdict: VerdictLiteral
    reason: str
    evidence: ValidationEvidence


class ChallengeReport(BaseModel):
    """Payload signed as a compact JWS (ES256). Never contains secrets or raw logs."""

    schema_version: Literal["settlebridge.challenge-report/v1"] = "settlebridge.challenge-report/v1"
    task_id: str
    repo: str
    pr_number: int | None
    base_sha: Sha
    target_sha: Sha
    contract_hash: str
    property_text: str
    environment: dict
    budget_cents: int
    participants: list[ReportParticipant]
    claims: list[ReportClaim]
    task_verdict: VerdictLiteral
    digests: dict[str, Digest]
    usage: dict[str, int]
    issued_at: datetime
    scope_note: str = "Under the stated property, environment, and budget."
