"""Challenge task state machine and job handlers (owner: P1-3).

Uses TASK_TRANSITIONS and CLAIM_TRANSITIONS from app.models.challenge and never moves
a task or claim outside them. Role and conflict rule: the challenger, the verifier,
and the adjudicator each have an operator different from the author and the solver,
and from each other. Platform workers use ``platform:challenger`` and
``platform:verifier``.

Moves are conditional updates (``WHERE state = <expected>``) so two workers cannot
both take the same step. Sandbox jobs never receive a fetch token.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tarfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError
from sqlalchemy.orm import object_session

from app.config import settings
from app.models.challenge import (
    CLAIM_TRANSITIONS,
    TASK_TRANSITIONS,
    TERMINAL_TASK_STATES,
    ArtifactKind,
    AttemptState,
    ChallengeArtifact,
    ChallengeAttempt,
    ChallengeClaim,
    ChallengeContract,
    ChallengeMode,
    ChallengeParticipant,
    ChallengeTask,
    ChallengeTaskState,
    ChallengeVerdict,
    ClaimState,
    DecidedBy,
    Finding,
    FindingKind,
    FindingStatus,
    ParticipantRole,
    VerdictKind,
    VerdictScope,
)
from app.models.github import GitHubJob, JobKind
from app.models.spend import ExecutionUsage, ReservationStatus, SpendReservation, UsageCause
from app.schemas.challenge import (
    ChallengeTaskCreate,
    ClaimIn,
    ResourceLimits,
    RunnerJob,
    RunnerResult,
    ValidationEvidence,
)
from app.services import challenge_runner_client, challenge_validity, github_scoped_token
from app.services.github_errors import PermanentJobError, RetryableJobError
from app.services.github_jobs import cancel_jobs, enqueue_job
from app.services.spend import (
    BudgetExceeded,
    BudgetMissing,
    BudgetPaused,
    reconcile_reservation,
    record_usage,
    release_reservation,
    reservation_exhausted,
    reserve_spend,
)

logger = logging.getLogger(__name__)

PLATFORM_CHALLENGER = "platform:challenger"
PLATFORM_VERIFIER = "platform:verifier"
_SCOPE_NOTE = "Under the stated property, environment, and budget."
_NO_FAILURE = "No failure found under the stated property, environment, and budget."
_UNKNOWN_DIGEST = "sha256:" + ("0" * 64)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_DEFAULT_LIMITS = {
    "cpu_seconds": 60,
    "memory_mb": 512,
    "wall_seconds": 120,
    "disk_mb": 1024,
    "max_processes": 128,
}
# ``target`` and ``base`` are the per-claim verifier runs. S0 names the earlier
# pre-claim runs ``baseline-target`` and ``baseline-base``; the claim runs stay
# distinct so their execution ids do not collide with that baseline.
_CAUSE_FOR = {
    "deps": UsageCause.DEPENDENCY_ACQUISITION.value,
    "baseline-base": UsageCause.INITIAL.value,
    "baseline-target": UsageCause.INITIAL.value,
    "attempt": UsageCause.INITIAL.value,
    "target": UsageCause.INITIAL.value,
    "positive": UsageCause.CONTROL.value,
    "negative": UsageCause.CONTROL.value,
    "base": UsageCause.INITIAL.value,
    "reference": UsageCause.CONTROL.value,
    "rederive": UsageCause.REDERIVATION.value,
    "rerun-1": UsageCause.RERUN.value,
    "rerun-2": UsageCause.RERUN.value,
    "rerun-3": UsageCause.RERUN.value,
    "minimized": UsageCause.RERUN.value,
}
_SEPARATED = {
    ParticipantRole.CHALLENGER.value,
    ParticipantRole.VERIFIER.value,
    ParticipantRole.ADJUDICATOR.value,
}
_CONFLICTS = {
    ParticipantRole.CHALLENGER.value: {
        ParticipantRole.AUTHOR.value,
        ParticipantRole.SOLVER.value,
        ParticipantRole.VERIFIER.value,
        ParticipantRole.ADJUDICATOR.value,
    },
    ParticipantRole.VERIFIER.value: {
        ParticipantRole.AUTHOR.value,
        ParticipantRole.SOLVER.value,
        ParticipantRole.CHALLENGER.value,
        ParticipantRole.ADJUDICATOR.value,
    },
    ParticipantRole.ADJUDICATOR.value: {
        ParticipantRole.AUTHOR.value,
        ParticipantRole.SOLVER.value,
        ParticipantRole.CHALLENGER.value,
        ParticipantRole.VERIFIER.value,
    },
    ParticipantRole.AUTHOR.value: _SEPARATED,
    ParticipantRole.SOLVER.value: _SEPARATED,
}


class InvalidTransition(Exception):
    pass


class RoleConflict(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


class _AttemptStopped(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Stage:
    task: ChallengeTask
    contract: ChallengeContract
    payload: dict
    attempt: ChallengeAttempt | None = None
    claim: ChallengeClaim | None = None
    reservation: SpendReservation | None = None
    execution_ids: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _content_hash(content: dict) -> str:
    # Same canonical hash as github_bounty_service.spec_content_hash.
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _json_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _reason(text: str) -> str:
    """Validate a reason. Reasons are stored and signed exactly as given."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a reason is required")
    if len(text) > 2000:
        raise ValueError("a reason must be at most 2000 characters")
    if "secure" in text.casefold():
        raise ValueError("a reason must not use the word secure")
    return text


def _diff_marker(base_sha: str, target_sha: str) -> str:
    # The fetcher returns one tarball per SHA, not a unified diff. The diff the
    # challenger is shown is the frozen base/target pair.
    digest = hashlib.sha256(f"{base_sha}:{target_sha}".encode()).hexdigest()
    return f"diff:{digest}"


def _detach(db: AsyncSession, obj: object) -> None:
    if object_session(obj) is not None:
        db.expunge(obj)


def _hash_file(path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _kind(job: GitHubJob) -> str:
    kind = getattr(job, "kind", "")
    value = getattr(kind, "value", kind)
    return str(value)


def _payload(job: GitHubJob) -> dict:
    payload = getattr(job, "payload", None)
    if not isinstance(payload, dict):
        raise PermanentJobError("challenge job payload is invalid")
    return payload


def _uuid(value: object, *, required: bool = False) -> uuid.UUID | None:
    if value in (None, ""):
        if required:
            raise PermanentJobError("challenge job payload is invalid")
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise PermanentJobError("challenge job payload is invalid") from None


async def _one(db: AsyncSession, model, row_id: uuid.UUID):
    stmt = select(model).where(model.id == row_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _require_task(db: AsyncSession, task_id: uuid.UUID) -> ChallengeTask:
    task = await _one(db, ChallengeTask, task_id)
    if task is None:
        raise LookupError("challenge task not found")
    return task


async def _refresh(db: AsyncSession, obj: object) -> None:
    refresh = getattr(db, "refresh", None)
    if refresh is None:
        return
    outcome = refresh(obj)
    if hasattr(outcome, "__await__"):
        await outcome


async def transition(
    db: AsyncSession, task: ChallengeTask, to_state: str, reason: str | None = None
) -> ChallengeTask:
    expected = task.state
    if to_state not in TASK_TRANSITIONS.get(expected, frozenset()):
        raise InvalidTransition(f"task cannot move from {expected} to {to_state}")
    values: dict = {"state": to_state, "updated_at": _now()}
    if reason is not None:
        values["state_reason"] = reason
    # A dirty in-memory state must not flush ahead of the conditional UPDATE.
    with db.no_autoflush:
        result = await db.execute(
            update(ChallengeTask)
            .where(ChallengeTask.id == task.id)
            .where(ChallengeTask.state == expected)
            .values(**values)
        )
    if int(result.rowcount or 0) != 1:
        raise InvalidTransition(f"task cannot move from {expected} to {to_state}")
    await _refresh(db, task)
    if task.state != to_state:
        task.state = to_state
        if reason is not None:
            task.state_reason = reason
    logger.info("challenge task %s moved to %s", task.id, to_state)
    return task


async def _transition_claim(
    db: AsyncSession, claim: ChallengeClaim, to_state: str
) -> ChallengeClaim:
    expected = claim.state
    if to_state not in CLAIM_TRANSITIONS.get(expected, frozenset()):
        raise InvalidTransition(f"claim cannot move from {expected} to {to_state}")
    with db.no_autoflush:
        result = await db.execute(
            update(ChallengeClaim)
            .where(ChallengeClaim.id == claim.id)
            .where(ChallengeClaim.state == expected)
            .values(state=to_state)
        )
    if int(result.rowcount or 0) != 1:
        raise InvalidTransition(f"claim cannot move from {expected} to {to_state}")
    await _refresh(db, claim)
    if claim.state != to_state:
        claim.state = to_state
    return claim


def _same_body(task: ChallengeTask, body: ChallengeTaskCreate) -> bool:
    return (
        task.repo_full_name == body.repo_full_name
        and task.pr_number == body.pr_number
        and task.base_sha == body.base_sha
        and task.target_sha == body.target_sha
        and task.property_text == body.property_text
        and list(task.failure_classes or []) == list(body.failure_classes)
        and int(task.budget_cents) == int(body.budget_cents)
        and _as_utc(task.deadline_at) == _as_utc(body.deadline_at)
    )


async def _task_by_key(db: AsyncSession, idempotency_key: str) -> ChallengeTask | None:
    stmt = select(ChallengeTask).where(ChallengeTask.idempotency_key == idempotency_key)
    return (await db.execute(stmt)).scalar_one_or_none()


async def create_task(
    db: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    installation_id: int,
    body: ChallengeTaskCreate,
    idempotency_key: str,
) -> ChallengeTask:
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("idempotency_key is invalid")
    existing = await _task_by_key(db, idempotency_key)
    if existing is not None:
        if not _same_body(existing, body):
            raise IdempotencyConflict("idempotency key was already used for a different challenge")
        return existing
    mode = (
        ChallengeMode.OPEN_PR.value
        if body.pr_number is not None
        else ChallengeMode.RETROSPECTIVE.value
    )
    task = ChallengeTask(
        id=uuid.uuid4(),
        mode=mode,
        owner_user_id=owner_user_id,
        installation_id=installation_id,
        repo_full_name=body.repo_full_name,
        pr_number=body.pr_number,
        base_sha=body.base_sha,
        target_sha=body.target_sha,
        property_text=body.property_text,
        failure_classes=list(body.failure_classes),
        environment={},
        budget_cents=body.budget_cents,
        deadline_at=_as_utc(body.deadline_at),
        state=ChallengeTaskState.DRAFT.value,
        idempotency_key=idempotency_key,
    )
    try:
        async with db.begin_nested():
            db.add(task)
            await db.flush()
    except IntegrityError:
        _detach(db, task)
        existing = await _task_by_key(db, idempotency_key)
        if existing is None:
            raise
        if not _same_body(existing, body):
            raise IdempotencyConflict(
                "idempotency key was already used for a different challenge"
            ) from None
        return existing
    return task


def _freeze_digest(explicit: object, path: object) -> str | None:
    if isinstance(path, str) and path:
        file = Path(path)
        if not file.is_file():
            raise ValueError("frozen digest bytes are missing")
        hexpart, _size = _hash_file(file)
        actual = f"sha256:{hexpart}"
        if explicit not in (None, actual, hexpart):
            raise ValueError("frozen digest does not match the bytes")
        return actual
    if explicit is None:
        return None
    if not isinstance(explicit, str):
        raise ValueError("frozen digest is invalid")
    return explicit


def _contract_content(task: ChallengeTask) -> tuple[dict, object, object]:
    raw = dict(task.environment or {})
    author = raw.pop("author_operator_id", None)
    solver = raw.pop("solver_operator_id", None)
    oracle_sources = raw.pop("oracle_sources", [])
    oracle_texts = raw.pop("oracle_texts", {})
    observers = raw.pop("observers", {})
    explicit_fixtures = raw.pop("fixtures_digest", None)
    explicit_observers = raw.pop("observers_digest", None)
    fixtures_digest = _freeze_digest(explicit_fixtures, raw.get("fixtures_path"))
    observers_digest = _freeze_digest(explicit_observers, raw.get("observers_path"))
    if observers_digest is None and observers:
        observers_digest = "sha256:" + _json_hash(observers)
    if not isinstance(oracle_sources, list):
        oracle_sources = []
    if not isinstance(observers, (dict, list)):
        observers = {}
    if not isinstance(oracle_texts, dict):
        oracle_texts = {}
    content = {
        "property": task.property_text,
        "failure_classes": list(task.failure_classes or []),
        "oracle_sources": oracle_sources,
        "oracle_texts": oracle_texts,
        "observers": observers,
        "environment": raw,
        "budget_cents": int(task.budget_cents),
        "deadline_at": _iso(task.deadline_at),
        "base_sha": task.base_sha,
        "target_sha": task.target_sha,
        "fixtures_digest": fixtures_digest,
        "observers_digest": observers_digest,
    }
    return content, author, solver


async def _next_version(db: AsyncSession, task_id: uuid.UUID) -> int:
    stmt = select(ChallengeContract.version).where(ChallengeContract.task_id == task_id)
    versions = (await db.execute(stmt)).scalars().all()
    if not versions:
        return 1
    return max(int(version) for version in versions) + 1


async def _enqueue(
    db: AsyncSession,
    *,
    kind: str,
    task: ChallengeTask,
    contract: ChallengeContract,
    stage: str,
    extra: dict | None = None,
) -> GitHubJob | None:
    if not settings.CHALLENGE_ENGINE_ENABLED:
        return None
    payload = {
        "task_id": str(task.id),
        "contract_id": str(contract.id),
        "contract_version": contract.version,
    }
    if extra:
        payload.update(extra)
    return await enqueue_job(
        db,
        kind=kind,
        payload=payload,
        dedupe_key=f"chal:{task.id}:{stage}:{contract.version}",
    )


async def _record_participant(
    db: AsyncSession,
    task: ChallengeTask,
    *,
    role: str,
    operator_id: str,
    artifacts_seen: list[str],
    platform_operated: bool = False,
) -> ChallengeParticipant:
    operator = str(operator_id).strip()
    if not operator:
        raise RoleConflict("role conflict")
    stmt = select(ChallengeParticipant).where(ChallengeParticipant.task_id == task.id)
    rows = list((await db.execute(stmt)).scalars().all())
    blocked = _CONFLICTS.get(role, frozenset())
    for row in rows:
        if row.operator_id == operator and row.role in blocked:
            raise RoleConflict(f"{role} conflicts with {row.role}")
        if row.role == role and row.operator_id == operator:
            merged = list(dict.fromkeys([*(row.artifacts_seen or []), *artifacts_seen]))
            if role == ParticipantRole.CHALLENGER.value:
                await _assert_challenger_seen(db, task.id, merged)
            row.artifacts_seen = merged
            await db.flush()
            return row
    if role == ParticipantRole.CHALLENGER.value:
        await _assert_challenger_seen(db, task.id, artifacts_seen)
    participant = ChallengeParticipant(
        id=uuid.uuid4(),
        task_id=task.id,
        role=role,
        operator_id=operator,
        platform_operated=platform_operated or operator.startswith("platform:"),
        conflict_declaration={},
        artifacts_seen=list(artifacts_seen),
    )
    try:
        async with db.begin_nested():
            db.add(participant)
            await db.flush()
    except IntegrityError:
        _detach(db, participant)
        rows = list((await db.execute(stmt)).scalars().all())
        for row in rows:
            if row.role == role and row.operator_id == operator:
                return row
        raise
    return participant


async def _assert_challenger_seen(db: AsyncSession, task_id: uuid.UUID, seen: list[str]) -> None:
    for item in seen:
        if item.startswith("contract:") or item.startswith("diff:"):
            continue
        try:
            artifact_id = uuid.UUID(item)
        except ValueError:
            raise RoleConflict("challenger saw material outside the frozen bundle") from None
        artifact = await _one(db, ChallengeArtifact, artifact_id)
        role = (artifact.meta or {}).get("role") if artifact is not None else None
        if (
            artifact is None
            or artifact.task_id != task_id
            or artifact.kind != ArtifactKind.SOURCE_TARBALL.value
            or role not in {"base", "target"}
        ):
            raise RoleConflict("challenger saw material outside the frozen bundle")


async def _challenger_seen(
    db: AsyncSession, task: ChallengeTask, contract: ChallengeContract
) -> list[str]:
    seen = [f"contract:{contract.id}", _diff_marker(task.base_sha, task.target_sha)]
    stmt = select(ChallengeArtifact).where(
        ChallengeArtifact.task_id == task.id,
        ChallengeArtifact.kind == ArtifactKind.SOURCE_TARBALL.value,
    )
    for artifact in (await db.execute(stmt)).scalars().all():
        if (artifact.meta or {}).get("role") in {"base", "target"}:
            seen.append(str(artifact.id))
    return seen


async def freeze_contract(db: AsyncSession, task_id: uuid.UUID) -> ChallengeTask:
    """Insert the next challenge_contracts version with its content hash; move to frozen."""
    task = await _require_task(db, task_id)
    if "frozen" not in TASK_TRANSITIONS.get(task.state, frozenset()):
        raise InvalidTransition(f"task cannot move from {task.state} to frozen")
    content, author, solver = _contract_content(task)
    version = await _next_version(db, task.id)
    contract = ChallengeContract(
        id=uuid.uuid4(),
        task_id=task.id,
        version=version,
        content_hash=_content_hash(content),
        content=content,
    )
    async with db.begin_nested():
        db.add(contract)
        await db.flush()
    task.current_contract_id = contract.id
    await db.flush()
    await transition(db, task, ChallengeTaskState.FROZEN.value, _reason("contract frozen"))
    if author:
        await _record_participant(
            db,
            task,
            role=ParticipantRole.AUTHOR.value,
            operator_id=str(author),
            artifacts_seen=[f"contract:{contract.id}"],
        )
    if solver:
        await _record_participant(
            db,
            task,
            role=ParticipantRole.SOLVER.value,
            operator_id=str(solver),
            artifacts_seen=[f"contract:{contract.id}"],
        )
    await _enqueue(
        db,
        kind=JobKind.CHALLENGE_FETCH.value,
        task=task,
        contract=contract,
        stage="fetch",
    )
    return task


async def _store_artifact(
    db: AsyncSession,
    task: ChallengeTask,
    *,
    kind: str,
    content_hash: str,
    size_bytes: int,
    storage_path: str,
    meta: dict,
) -> ChallengeArtifact:
    stmt = select(ChallengeArtifact).where(
        ChallengeArtifact.task_id == task.id,
        ChallengeArtifact.content_hash == content_hash,
        ChallengeArtifact.kind == kind,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing
    row = ChallengeArtifact(
        id=uuid.uuid4(),
        task_id=task.id,
        tenant_user_id=task.owner_user_id,
        kind=kind,
        content_hash=content_hash,
        size_bytes=size_bytes,
        storage_path=storage_path[:512],
        meta=meta,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        _detach(db, row)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is None:
            raise
        return existing
    return row


def _split_digest(value: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise PermanentJobError("source digest is invalid")
    return value.split(":", 1)[1]


async def _digest_for_role(
    db: AsyncSession, task_id: uuid.UUID, role: str, kind: str
) -> str | None:
    stmt = select(ChallengeArtifact).where(
        ChallengeArtifact.task_id == task_id,
        ChallengeArtifact.kind == kind,
    )
    for artifact in (await db.execute(stmt)).scalars().all():
        if (artifact.meta or {}).get("role") == role:
            return f"sha256:{artifact.content_hash}"
    return None


def _claim_hash(claim: ClaimIn) -> str:
    encoded = json.dumps(claim.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _claim_in(claim: ChallengeClaim) -> ClaimIn:
    return ClaimIn.model_validate(
        {
            "failure_class": claim.failure_class,
            "scenario": claim.scenario,
            "oracle_citation": claim.oracle_citation,
            "expected": claim.expected,
            "observed": claim.observed,
            "claimed_impact": claim.claimed_impact,
        }
    )


async def _claims_for_task(db: AsyncSession, task_id: uuid.UUID) -> list[ChallengeClaim]:
    stmt = select(ChallengeClaim).where(ChallengeClaim.task_id == task_id)
    return list((await db.execute(stmt)).scalars().all())


async def _has_verdict(db: AsyncSession, claim_id: uuid.UUID) -> bool:
    stmt = select(ChallengeVerdict.id).where(ChallengeVerdict.claim_id == claim_id).limit(1)
    return (await db.execute(stmt)).first() is not None


async def _latest_verdict(db: AsyncSession, claim_id: uuid.UUID) -> ChallengeVerdict | None:
    stmt = select(ChallengeVerdict).where(ChallengeVerdict.claim_id == claim_id)
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return None
    return rows[-1]


async def _ensure_finding(
    db: AsyncSession, task: ChallengeTask, claim: ChallengeClaim, verdict: ChallengeVerdict
) -> None:
    dedupe_key = f"chal:{task.repo_full_name}:{claim.failure_class}:{claim.claim_hash}"
    stmt = select(Finding).where(
        Finding.tenant_user_id == task.owner_user_id,
        Finding.dedupe_key == dedupe_key,
    )
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        return
    title = " ".join(claim.claimed_impact.split())[:255] or claim.failure_class
    finding = Finding(
        id=uuid.uuid4(),
        tenant_user_id=task.owner_user_id,
        kind=FindingKind.CHALLENGE_GAP.value,
        repo_full_name=task.repo_full_name,
        task_id=task.id,
        claim_id=claim.id,
        verdict_id=verdict.id,
        status=FindingStatus.NEEDS_DECISION.value,
        failure_class=claim.failure_class,
        title=title,
        target_sha=task.target_sha,
        dedupe_key=dedupe_key,
    )
    try:
        async with db.begin_nested():
            db.add(finding)
            await db.flush()
    except IntegrityError:
        _detach(db, finding)


async def _append_verdict(
    db: AsyncSession,
    *,
    task: ChallengeTask,
    contract: ChallengeContract,
    claim: ChallengeClaim | None,
    verdict: str,
    decided_by: str,
    reason: str,
    evidence: ValidationEvidence | dict,
    adjudicator_user_id: uuid.UUID | None = None,
    allow_duplicate: bool = False,
) -> ChallengeVerdict | None:
    if claim is not None and not allow_duplicate and await _has_verdict(db, claim.id):
        return await _latest_verdict(db, claim.id)
    if hasattr(evidence, "model_dump"):
        payload = evidence.model_dump(mode="json")
    else:
        payload = dict(evidence or {})
    row = ChallengeVerdict(
        id=uuid.uuid4(),
        task_id=task.id,
        claim_id=None if claim is None else claim.id,
        contract_id=contract.id,
        scope=VerdictScope.CLAIM.value if claim is not None else VerdictScope.TASK.value,
        verdict=verdict,
        decided_by=decided_by,
        adjudicator_user_id=adjudicator_user_id,
        reason=_reason(reason),
        evidence=payload,
    )
    db.add(row)
    await db.flush()
    if claim is not None and verdict == VerdictKind.CONFIRMED.value:
        await _ensure_finding(db, task, claim, row)
    return row


def _early_evidence(
    *, citation_valid: bool, tamper_clean: bool, execution_ids: list[str]
) -> ValidationEvidence:
    return ValidationEvidence(
        citation_valid=citation_valid,
        tamper_clean=tamper_clean,
        observer_fired=False,
        execution_ids=list(execution_ids),
        needs_adjudication=False,
    )


async def _append_task_verdict(
    db: AsyncSession, stage: "_Stage", verdict: str, reason: str
) -> None:
    await _append_verdict(
        db,
        task=stage.task,
        contract=stage.contract,
        claim=None,
        verdict=verdict,
        decided_by=DecidedBy.MACHINE.value,
        reason=reason,
        evidence=_early_evidence(
            citation_valid=True, tamper_clean=True, execution_ids=stage.execution_ids
        ),
    )


async def _bundle_for_attempt(
    db: AsyncSession, task_id: uuid.UUID, attempt_id: uuid.UUID
) -> str | None:
    stmt = select(ChallengeArtifact).where(
        ChallengeArtifact.task_id == task_id,
        ChallengeArtifact.kind == ArtifactKind.CHALLENGER_TEST.value,
    )
    matched = []
    for artifact in (await db.execute(stmt)).scalars().all():
        if str((artifact.meta or {}).get("attempt_id")) == str(attempt_id):
            matched.append(artifact)
    if not matched:
        return None
    return matched[-1].storage_path


def _under_challenge_tests(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name:
        return False
    parts = PurePosixPath(name).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return False
    return parts[0] == "challenge_tests"


def _bundle_only_challenge_tests(path: str) -> bool:
    try:
        tar = tarfile.open(path, "r:*")
    except (tarfile.TarError, OSError):
        return False
    try:
        members = tar.getmembers()
    except tarfile.TarError:
        return False
    finally:
        tar.close()
    if not members or len(members) > 10000:
        return False
    files = 0
    for member in members:
        if member.issym() or member.islnk() or member.ischr() or member.isblk():
            return False
        if not (member.isdir() or member.isreg()):
            return False
        if not _under_challenge_tests(member.name):
            return False
        if member.isreg():
            files += 1
    return files >= 1


def _digest_unchanged(frozen: object, path: object) -> bool:
    if not path:
        return frozen is None or isinstance(frozen, str)
    if not isinstance(path, str) or not isinstance(frozen, str):
        return False
    from pathlib import Path

    file = Path(path)
    if not file.is_file():
        return False
    try:
        hexpart, _size = _hash_file(file)
    except OSError:
        return False
    return frozen in {f"sha256:{hexpart}", hexpart}


def _audit_tamper(content: dict, bundle_path: str | None) -> tuple[bool, str]:
    if not bundle_path:
        return False, "attempt artifact is missing"
    from pathlib import Path

    if not Path(bundle_path).is_file() or not _bundle_only_challenge_tests(bundle_path):
        return False, "attempt artifact writes outside challenge_tests"
    env = content.get("environment") if isinstance(content.get("environment"), dict) else {}
    if not _digest_unchanged(content.get("fixtures_digest"), env.get("fixtures_path")):
        return False, "frozen fixtures changed"
    if not _digest_unchanged(content.get("observers_digest"), env.get("observers_path")):
        return False, "frozen observers changed"
    return True, "tamper audit clean"


async def _reject_claim(
    db: AsyncSession,
    *,
    task: ChallengeTask,
    contract: ChallengeContract,
    claim: ChallengeClaim,
    to_state: str,
    verdict: str,
    reason: str,
    citation_valid: bool,
    tamper_clean: bool,
) -> ChallengeClaim:
    await _append_verdict(
        db,
        task=task,
        contract=contract,
        claim=claim,
        verdict=verdict,
        decided_by=DecidedBy.MACHINE.value,
        reason=reason,
        evidence=_early_evidence(
            citation_valid=citation_valid, tamper_clean=tamper_clean, execution_ids=[]
        ),
    )
    return await _transition_claim(db, claim, to_state)


async def submit_claim(
    db: AsyncSession, *, task_id: uuid.UUID, attempt_id: uuid.UUID, claim: ClaimIn
) -> ChallengeClaim:
    task = await _require_task(db, task_id)
    attempt = await _one(db, ChallengeAttempt, attempt_id)
    if attempt is None or attempt.task_id != task.id:
        raise LookupError("challenge attempt not found")
    if task.state not in {
        ChallengeTaskState.ATTEMPTING.value,
        ChallengeTaskState.VALIDATING.value,
    }:
        raise InvalidTransition(f"task cannot move from {task.state} to validating")
    contract = await _one(db, ChallengeContract, attempt.contract_id)
    if contract is None:
        raise LookupError("challenge contract not found")
    digest = _claim_hash(claim)
    duplicate = any(row.claim_hash == digest for row in await _claims_for_task(db, task.id))
    row = ChallengeClaim(
        id=uuid.uuid4(),
        task_id=task.id,
        attempt_id=attempt.id,
        contract_id=contract.id,
        failure_class=claim.failure_class,
        scenario=claim.scenario.model_dump(),
        oracle_citation=claim.oracle_citation.model_dump(),
        expected=claim.expected,
        observed=claim.observed,
        claimed_impact=claim.claimed_impact,
        state=ClaimState.SUBMITTED.value,
        claim_hash=digest,
    )
    db.add(row)
    await db.flush()
    if duplicate:
        return await _reject_claim(
            db,
            task=task,
            contract=contract,
            claim=row,
            to_state=ClaimState.REJECTED_PRECHECK.value,
            verdict=VerdictKind.INVALID_OR_DUPLICATE.value,
            reason="duplicate claim",
            citation_valid=False,
            tamper_clean=True,
        )
    ok, _detail = challenge_validity.precheck_citation(claim, contract.content)
    if not ok:
        return await _reject_claim(
            db,
            task=task,
            contract=contract,
            claim=row,
            to_state=ClaimState.REJECTED_PRECHECK.value,
            verdict=VerdictKind.INVALID_OR_DUPLICATE.value,
            reason="citation is not valid for the frozen contract",
            citation_valid=False,
            tamper_clean=True,
        )
    bundle = await _bundle_for_attempt(db, task.id, attempt.id)
    clean, audit_reason = _audit_tamper(contract.content, bundle)
    if not clean:
        return await _reject_claim(
            db,
            task=task,
            contract=contract,
            claim=row,
            to_state=ClaimState.TAMPER_REJECTED.value,
            verdict=VerdictKind.INVALID_OR_DUPLICATE.value,
            reason=audit_reason,
            citation_valid=True,
            tamper_clean=False,
        )
    await _transition_claim(db, row, ClaimState.VALIDATING.value)
    if task.state == ChallengeTaskState.ATTEMPTING.value:
        await transition(
            db, task, ChallengeTaskState.VALIDATING.value, _reason("validating claims")
        )
    await _enqueue(
        db,
        kind=JobKind.CHALLENGE_VALIDATE.value,
        task=task,
        contract=contract,
        stage=f"validate:{row.id}",
        extra={"claim_id": str(row.id), "attempt_id": str(attempt.id)},
    )
    return row


async def _reserve(
    db: AsyncSession, task: ChallengeTask, subject: str, version: int
) -> SpendReservation:
    try:
        return await reserve_spend(
            db,
            user_id=task.owner_user_id,
            subject=subject,
            idempotency_key=f"spend:{subject}:{version}",
            cents=int(task.budget_cents),
            expires_at=_as_utc(task.deadline_at),
        )
    except BudgetExceeded:
        raise _AttemptStopped("budget exceeded") from None
    except BudgetPaused:
        raise _AttemptStopped("budget paused") from None
    except BudgetMissing:
        raise _AttemptStopped("budget missing") from None


async def _reload_reservation(db: AsyncSession, reservation_id: uuid.UUID) -> SpendReservation:
    row = await _one(db, SpendReservation, reservation_id)
    if row is None:
        raise _AttemptStopped("reservation is not open")
    return row


async def _reconcile(db: AsyncSession, reservation: SpendReservation | None) -> None:
    if reservation is None or reservation.status != ReservationStatus.RESERVED.value:
        return
    await reconcile_reservation(db, reservation.id)


async def _next_execution_id(
    db: AsyncSession, task_id: uuid.UUID, purpose: str, reservation_id: uuid.UUID
) -> str:
    scope = reservation_id.hex[:12]
    for number in range(1, 1000):
        candidate = f"exec:{task_id}:{purpose}:{scope}-{number}"
        stmt = select(ExecutionUsage.id).where(ExecutionUsage.execution_id == candidate)
        if (await db.execute(stmt)).first() is None:
            return candidate
    raise PermanentJobError("execution id space exhausted")


def _argv(value: object) -> list[str]:
    if isinstance(value, str):
        return value.split() if value else []
    if isinstance(value, list):
        return [str(part) for part in value]
    return []


def _limits(env: dict) -> ResourceLimits:
    raw = dict(_DEFAULT_LIMITS)
    custom = env.get("limits")
    if isinstance(custom, dict):
        for key in raw:
            if key in custom:
                raw[key] = custom[key]
    return ResourceLimits(**raw)


def _observers_for(content: dict, failure_class: str) -> list[str]:
    raw = content.get("observers")
    if not raw:
        return []
    if isinstance(raw, dict):
        picked = raw.get(failure_class, raw.get("*", []))
        if isinstance(picked, str):
            return [picked] if picked else []
        if isinstance(picked, list):
            return [str(item) for item in picked if str(item)]
        return []
    if isinstance(raw, list):
        if all(isinstance(item, str) for item in raw):
            return [item for item in raw if item]
        names = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            if item.get("failure_class") not in (failure_class, "*"):
                continue
            name = item.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names
    return []


def _observer_names(content: dict, failure_class: str | None) -> list[str]:
    if failure_class:
        return _observers_for(content, failure_class)
    names: list[str] = []
    for item in content.get("failure_classes") or []:
        names.extend(_observers_for(content, str(item)))
    return list(dict.fromkeys(names))


def _test_command(env: dict, purpose: str, claim: ClaimIn | None) -> list[str]:
    commands = env.get("commands")
    if isinstance(commands, dict) and purpose in commands:
        return _argv(commands[purpose])
    if purpose == "rederive":
        return _argv(env.get("rederive_command")) or ["pytest", "-q"]
    base = _argv(env.get("test_command"))
    if claim is not None:
        path = claim.scenario.test_path
        if path not in base:
            return [*base, path] if base else ["pytest", "-q", path]
    return base


def _mount_verifier(purpose: str) -> bool:
    return purpose not in {"deps", "attempt"}


def _include_bundle(purpose: str) -> bool:
    return purpose not in {"deps", "attempt", "rederive", "baseline-base", "baseline-target"}


def _runner_job(
    stage: _Stage,
    purpose: str,
    execution_id: str,
    source_digest: str,
    *,
    claim: ClaimIn | None,
    bundle_digest: str | None,
    wheelhouse: str | None,
) -> RunnerJob:
    content = stage.contract.content
    env = content.get("environment") if isinstance(content.get("environment"), dict) else {}
    fixtures = content.get("fixtures_digest") if _mount_verifier(purpose) else None
    canaries = env.get("canaries_digest") if _mount_verifier(purpose) else None
    if fixtures is not None and _DIGEST.fullmatch(str(fixtures)) is None:
        raise _AttemptStopped("frozen digest is invalid")
    if canaries is not None and _DIGEST.fullmatch(str(canaries)) is None:
        canaries = None
    observers = _observer_names(content, claim.failure_class if claim else None)
    if not _mount_verifier(purpose):
        observers = []
    return RunnerJob(
        execution_id=execution_id,
        stage="dependencies" if purpose == "deps" else "execute",
        task_id=str(stage.task.id),
        source_tarball=source_digest,
        wheelhouse_manifest=None if purpose == "deps" else wheelhouse,
        fixtures_digest=fixtures if isinstance(fixtures, str) else None,
        challenger_bundle=bundle_digest if _include_bundle(purpose) else None,
        python_version=str(env.get("python") or "3.11"),
        install_command=_argv(env.get("install")),
        test_command=[] if purpose == "deps" else _test_command(env, purpose, claim),
        allowlist_hosts=["pypi.org", "files.pythonhosted.org"] if purpose == "deps" else [],
        limits=_limits(env),
        observers=observers,
        canaries_digest=canaries if isinstance(canaries, str) else None,
    )


def _sandbox_result(job: RunnerJob) -> RunnerResult:
    return RunnerResult(
        execution_id=job.execution_id,
        stage=job.stage,
        status="sandbox_error",
        image_digest=_UNKNOWN_DIGEST,
        source_digest=job.source_tarball,
        error="sandbox unavailable",
    )


async def _invoke(job: RunnerJob) -> RunnerResult:
    try:
        outcome = challenge_runner_client.run_runner(
            job, artifact_root=settings.CHALLENGE_ARTIFACT_ROOT
        )
        if hasattr(outcome, "__await__"):
            outcome = await outcome
    except challenge_runner_client.RunnerInvocationError:
        return _sandbox_result(job)
    if isinstance(outcome, RunnerResult):
        return outcome
    return RunnerResult.model_validate(outcome)


async def _store_log(
    db: AsyncSession, task: ChallengeTask, execution_id: str, result: RunnerResult
) -> None:
    digest = result.log_digest
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        return
    await _store_artifact(
        db,
        task,
        kind=ArtifactKind.RUN_LOG.value,
        content_hash=digest.split(":", 1)[1],
        size_bytes=0,
        storage_path=f"digest:{digest}",
        meta={"execution_id": execution_id, "status": result.status},
    )


async def _charge(
    db: AsyncSession,
    stage: _Stage,
    execution_id: str,
    cause: str,
    result: RunnerResult,
) -> None:
    if stage.reservation is None or stage.reservation.status != ReservationStatus.RESERVED.value:
        raise _AttemptStopped("reservation is not open")
    await record_usage(
        db,
        reservation_id=stage.reservation.id,
        execution_id=execution_id,
        cause=cause,
        cpu_seconds=int(result.cpu_seconds or 0),
        wall_seconds=int(result.wall_seconds or 0),
        model_tokens=0,
        cost_cents=max(int(result.cpu_seconds or 0), 1),
    )
    stage.execution_ids.append(execution_id)
    await _store_log(db, stage.task, execution_id, result)
    stage.reservation = await _reload_reservation(db, stage.reservation.id)


async def _run_purpose(
    db: AsyncSession,
    stage: _Stage,
    purpose: str,
    source_digest: str,
    *,
    claim: ClaimIn | None = None,
    bundle_digest: str | None = None,
    wheelhouse: str | None = None,
) -> RunnerResult:
    if stage.reservation is None or reservation_exhausted(stage.reservation):
        raise _AttemptStopped("budget exhausted")

    async def once(cause: str) -> RunnerResult:
        execution_id = await _next_execution_id(db, stage.task.id, purpose, stage.reservation.id)
        job = _runner_job(
            stage,
            purpose,
            execution_id,
            source_digest,
            claim=claim,
            bundle_digest=bundle_digest,
            wheelhouse=wheelhouse,
        )
        result = await _invoke(job)
        recorded = "platform_retry" if result.status == "sandbox_error" else cause
        await _charge(db, stage, execution_id, recorded, result)
        return result

    result = await once(_CAUSE_FOR[purpose])
    if result.status == "sandbox_error":
        if reservation_exhausted(stage.reservation):
            raise _AttemptStopped("budget exhausted")
        result = await once(UsageCause.PLATFORM_RETRY.value)
        if result.status == "sandbox_error":
            raise _AttemptStopped("sandbox unavailable")
    if result.status in {"limit_exceeded", "cancelled"}:
        raise _AttemptStopped(result.status.replace("_", " "))
    if result.status not in {"ok", "test_failed"}:
        raise _AttemptStopped("runner failed")
    return result


async def _stop_task(db: AsyncSession, stage: _Stage, reason: str) -> None:
    task = stage.task
    if task.state in TERMINAL_TASK_STATES:
        await _reconcile(db, stage.reservation)
        return
    explained = _reason(reason)
    if stage.claim is not None and stage.claim.state == ClaimState.VALIDATING.value:
        await _append_verdict(
            db,
            task=task,
            contract=stage.contract,
            claim=stage.claim,
            verdict=VerdictKind.INCONCLUSIVE.value,
            decided_by=DecidedBy.MACHINE.value,
            reason=reason,
            evidence=_early_evidence(
                citation_valid=True,
                tamper_clean=True,
                execution_ids=stage.execution_ids,
            ),
        )
        await _transition_claim(db, stage.claim, ClaimState.DECIDED.value)
    if stage.attempt is not None and stage.attempt.state in {
        AttemptState.QUEUED.value,
        AttemptState.RUNNING.value,
    }:
        stage.attempt.state = AttemptState.FAILED.value
        stage.attempt.error = explained[:500]
        stage.attempt.finished_at = _now()
    if task.state == ChallengeTaskState.OPEN.value:
        await transition(db, task, ChallengeTaskState.ATTEMPTING.value, explained)
    if task.state == ChallengeTaskState.BASELINING.value:
        await transition(db, task, ChallengeTaskState.INCONCLUSIVE.value, explained)
    elif task.state == ChallengeTaskState.ATTEMPTING.value:
        await transition(db, task, ChallengeTaskState.INCONCLUSIVE.value, explained)
    elif task.state == ChallengeTaskState.VALIDATING.value:
        await transition(db, task, ChallengeTaskState.INCONCLUSIVE.value, explained)
    elif task.state == ChallengeTaskState.ADJUDICATING.value:
        await transition(db, task, ChallengeTaskState.INCONCLUSIVE.value, explained)
    await _reconcile(db, stage.reservation)
    if task.state == ChallengeTaskState.INCONCLUSIVE.value:
        await _append_task_verdict(db, stage, VerdictKind.INCONCLUSIVE.value, reason)
    if task.state in {ChallengeTaskState.INCONCLUSIVE.value, ChallengeTaskState.CLOSED.value}:
        await _enqueue(
            db,
            kind=JobKind.CHALLENGE_REPORT.value,
            task=task,
            contract=stage.contract,
            stage="report",
        )


async def _open_stage(db: AsyncSession, job: GitHubJob, *, expected: str | None) -> _Stage | None:
    payload = _payload(job)
    task_id = _uuid(payload.get("task_id"), required=True)
    task = await _one(db, ChallengeTask, task_id)
    if task is None:
        raise PermanentJobError("challenge task not found")
    if task.state in TERMINAL_TASK_STATES:
        return None
    contract_id = _uuid(payload.get("contract_id")) or task.current_contract_id
    if contract_id is None:
        raise PermanentJobError("challenge contract not found")
    contract = await _one(db, ChallengeContract, contract_id)
    if contract is None or contract.task_id != task.id:
        raise PermanentJobError("challenge contract not found")
    if expected is not None and task.state != expected:
        return None
    return _Stage(task=task, contract=contract, payload=payload)


async def _remember_fetch(
    db: AsyncSession, task: ChallengeTask, *, role: str, sha: str, fetched
) -> None:
    hexpart = _split_digest(fetched.source_digest)
    from pathlib import Path

    file = Path(fetched.source_tarball)
    size = file.stat().st_size if file.is_file() else 0
    await _store_artifact(
        db,
        task,
        kind=ArtifactKind.SOURCE_TARBALL.value,
        content_hash=hexpart,
        size_bytes=size,
        storage_path=str(file),
        meta={"role": role, "sha": sha},
    )
    if fetched.history_digest and fetched.history_bundle:
        history_hex = _split_digest(fetched.history_digest)
        await _store_artifact(
            db,
            task,
            kind=ArtifactKind.HISTORY_BUNDLE.value,
            content_hash=history_hex,
            size_bytes=0,
            storage_path=str(fetched.history_bundle)[:512],
            meta={"role": role, "sha": sha},
        )


async def _handle_fetch(db: AsyncSession, job: GitHubJob) -> None:
    stage = await _open_stage(db, job, expected=ChallengeTaskState.FROZEN.value)
    if stage is None:
        return
    await transition(
        db, stage.task, ChallengeTaskState.BASELINING.value, _reason("fetching source")
    )
    from pathlib import Path

    workdir = Path(settings.CHALLENGE_ARTIFACT_ROOT) / "fetch" / str(stage.task.id)
    workdir.mkdir(parents=True, exist_ok=True)
    token = await github_scoped_token.scoped_read_token(
        stage.task.installation_id, stage.task.repo_full_name
    )
    include_history = stage.task.mode == ChallengeMode.RETROSPECTIVE.value
    try:
        if not isinstance(token, str) or token == "":
            raise challenge_runner_client.RunnerInvocationError("source fetch failed")
        for role, sha in (("target", stage.task.target_sha), ("base", stage.task.base_sha)):
            fetched = challenge_runner_client.fetch_source(
                repo_full_name=stage.task.repo_full_name,
                sha=sha,
                token=token,
                workdir=str(workdir),
                include_history=include_history,
            )
            if hasattr(fetched, "__await__"):
                fetched = await fetched
            await _remember_fetch(db, stage.task, role=role, sha=sha, fetched=fetched)
    except PermanentJobError:
        raise
    except Exception:
        raise RetryableJobError("source fetch failed") from None
    finally:
        token = ""
    await _enqueue(
        db,
        kind=JobKind.CHALLENGE_DEPS.value,
        task=stage.task,
        contract=stage.contract,
        stage="deps",
    )


async def _source_or_stop(db: AsyncSession, task_id: uuid.UUID, role: str) -> str:
    digest = await _digest_for_role(db, task_id, role, ArtifactKind.SOURCE_TARBALL.value)
    if digest is None:
        raise PermanentJobError("source artifact is missing")
    return digest


async def _wheelhouse(db: AsyncSession, task_id: uuid.UUID, role: str) -> str | None:
    return await _digest_for_role(db, task_id, role, ArtifactKind.WHEELHOUSE_MANIFEST.value)


async def _store_wheelhouse(
    db: AsyncSession, task: ChallengeTask, role: str, digest: str | None
) -> None:
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        return
    await _store_artifact(
        db,
        task,
        kind=ArtifactKind.WHEELHOUSE_MANIFEST.value,
        content_hash=digest.split(":", 1)[1],
        size_bytes=0,
        storage_path=f"digest:{digest}",
        meta={"role": role},
    )


async def _handle_deps(db: AsyncSession, job: GitHubJob) -> None:
    stage = await _open_stage(db, job, expected=ChallengeTaskState.BASELINING.value)
    if stage is None:
        return
    try:
        stage.reservation = await _reserve(
            db, stage.task, f"challenge_baseline:{stage.task.id}", stage.contract.version
        )
        for role in ("target", "base"):
            if reservation_exhausted(stage.reservation):
                raise _AttemptStopped("budget exhausted")
            source = await _source_or_stop(db, stage.task.id, role)
            result = await _run_purpose(db, stage, "deps", source)
            await _store_wheelhouse(db, stage.task, role, result.wheelhouse_manifest)
        await _enqueue(
            db,
            kind=JobKind.CHALLENGE_BASELINE.value,
            task=stage.task,
            contract=stage.contract,
            stage="baseline",
        )
    except _AttemptStopped as stopped:
        await _stop_task(db, stage, stopped.reason)


async def _handle_baseline(db: AsyncSession, job: GitHubJob) -> None:
    stage = await _open_stage(db, job, expected=ChallengeTaskState.BASELINING.value)
    if stage is None:
        return
    try:
        stage.reservation = await _reserve(
            db, stage.task, f"challenge_baseline:{stage.task.id}", stage.contract.version
        )
        for purpose, role in (("baseline-base", "base"), ("baseline-target", "target")):
            if reservation_exhausted(stage.reservation):
                raise _AttemptStopped("budget exhausted")
            source = await _source_or_stop(db, stage.task.id, role)
            wheel = await _wheelhouse(db, stage.task.id, role)
            await _run_purpose(db, stage, purpose, source, wheelhouse=wheel)
        await _reconcile(db, stage.reservation)
        await transition(
            db, stage.task, ChallengeTaskState.OPEN.value, _reason("baseline recorded")
        )
        attempt_id = uuid.uuid4()
        await _enqueue(
            db,
            kind=JobKind.CHALLENGE_ATTEMPT.value,
            task=stage.task,
            contract=stage.contract,
            stage=f"attempt:{attempt_id}",
            extra={"attempt_id": str(attempt_id)},
        )
    except _AttemptStopped as stopped:
        await _stop_task(db, stage, stopped.reason)


async def _ensure_attempt(db: AsyncSession, stage: _Stage) -> ChallengeAttempt:
    attempt_id = _uuid(stage.payload.get("attempt_id")) or uuid.uuid4()
    existing = await _one(db, ChallengeAttempt, attempt_id)
    if existing is not None:
        if existing.task_id != stage.task.id:
            raise PermanentJobError("challenge attempt not found")
        return existing
    seen = await _challenger_seen(db, stage.task, stage.contract)
    operator = str(stage.payload.get("challenger_operator") or PLATFORM_CHALLENGER)
    participant = await _record_participant(
        db,
        stage.task,
        role=ParticipantRole.CHALLENGER.value,
        operator_id=operator,
        artifacts_seen=seen,
        platform_operated=operator.startswith("platform:"),
    )
    attempt = ChallengeAttempt(
        id=attempt_id,
        task_id=stage.task.id,
        participant_id=participant.id,
        contract_id=stage.contract.id,
        state=AttemptState.QUEUED.value,
    )
    db.add(attempt)
    await db.flush()
    return attempt


async def _record_bundle(db: AsyncSession, stage: _Stage) -> None:
    raw = stage.payload.get("bundle_path")
    if not isinstance(raw, str) or raw == "":
        return
    from pathlib import Path

    file = Path(raw)
    if not file.is_file():
        return
    hexpart, size = _hash_file(file)
    await _store_artifact(
        db,
        stage.task,
        kind=ArtifactKind.CHALLENGER_TEST.value,
        content_hash=hexpart,
        size_bytes=size,
        storage_path=str(file),
        meta={"attempt_id": str(stage.attempt.id), "role": "challenger_bundle"},
    )


def _claims_from_payload(payload: dict) -> list[ClaimIn]:
    raw = payload.get("claims")
    if not raw:
        return []
    if not isinstance(raw, list):
        raise PermanentJobError("challenge job payload is invalid")
    try:
        return [ClaimIn.model_validate(item) for item in raw]
    except ValidationError:
        raise PermanentJobError("challenge job payload is invalid") from None


async def _finish_attempt(db: AsyncSession, stage: _Stage, *, no_failure: bool) -> None:
    claims: list[ChallengeClaim] = []
    if stage.attempt is not None:
        claims = [
            row
            for row in await _claims_for_task(db, stage.task.id)
            if row.attempt_id == stage.attempt.id
        ]
    validating = any(row.state == ClaimState.VALIDATING.value for row in claims)
    rejected = bool(claims) and all(
        row.state in {ClaimState.REJECTED_PRECHECK.value, ClaimState.TAMPER_REJECTED.value}
        for row in claims
    )
    if not claims and not no_failure and not validating:
        raise _AttemptStopped("attempt failed without a structured claim")
    if stage.attempt is not None:
        stage.attempt.state = AttemptState.SUBMITTED.value
        stage.attempt.finished_at = _now()
    await _reconcile(db, stage.reservation)
    stage.reservation = None
    if validating or stage.task.state != ChallengeTaskState.ATTEMPTING.value:
        return
    if no_failure and not claims:
        await _append_verdict(
            db,
            task=stage.task,
            contract=stage.contract,
            claim=None,
            verdict=VerdictKind.NO_FAILURE_FOUND.value,
            decided_by=DecidedBy.MACHINE.value,
            reason=_NO_FAILURE,
            evidence=_early_evidence(
                citation_valid=False, tamper_clean=True, execution_ids=stage.execution_ids
            ),
        )
        await transition(db, stage.task, ChallengeTaskState.OPEN.value, _reason(_NO_FAILURE))
        await transition(db, stage.task, ChallengeTaskState.CLOSED.value, _reason(_NO_FAILURE))
    elif rejected:
        await transition(db, stage.task, ChallengeTaskState.OPEN.value, _reason("no valid claim"))
        await transition(db, stage.task, ChallengeTaskState.CLOSED.value, _reason("no valid claim"))
    else:
        return
    await _enqueue(
        db,
        kind=JobKind.CHALLENGE_REPORT.value,
        task=stage.task,
        contract=stage.contract,
        stage="report",
    )


async def _handle_attempt(db: AsyncSession, job: GitHubJob) -> None:
    stage = await _open_stage(db, job, expected=None)
    if stage is None:
        return
    if stage.task.state == ChallengeTaskState.OPEN.value:
        await transition(
            db, stage.task, ChallengeTaskState.ATTEMPTING.value, _reason("challenger attempt")
        )
    if stage.task.state != ChallengeTaskState.ATTEMPTING.value:
        return
    try:
        stage.attempt = await _ensure_attempt(db, stage)
        if stage.attempt.state in {
            AttemptState.SUBMITTED.value,
            AttemptState.FAILED.value,
            AttemptState.CANCELLED.value,
        }:
            return
        stage.attempt.state = AttemptState.RUNNING.value
        stage.attempt.started_at = _now()
        stage.reservation = await _reserve(
            db,
            stage.task,
            f"challenge_attempt:{stage.attempt.id}",
            stage.contract.version,
        )
        stage.attempt.reservation_id = stage.reservation.id
        source = await _source_or_stop(db, stage.task.id, "target")
        wheel = await _wheelhouse(db, stage.task.id, "target")
        result = await _run_purpose(db, stage, "attempt", source, wheelhouse=wheel)
        await _record_bundle(db, stage)
        claims = _claims_from_payload(stage.payload)
        for claim in claims:
            await submit_claim(db, task_id=stage.task.id, attempt_id=stage.attempt.id, claim=claim)
        await _finish_attempt(db, stage, no_failure=result.status == "ok" and not claims)
    except _AttemptStopped as stopped:
        await _stop_task(db, stage, stopped.reason)
    except RoleConflict:
        await _stop_task(db, stage, "role conflict")


def _validation_purposes(content: dict) -> list[str]:
    purposes = ["target", "positive", "negative", "base"]
    reference = content.get("environment") or {}
    if isinstance(reference, dict) and reference.get("reference_digest"):
        purposes.append("reference")
    elif content.get("reference_digest"):
        purposes.append("reference")
    purposes.extend(["rederive", "rerun-1", "rerun-2", "rerun-3", "minimized"])
    return purposes


def _reference_digest(content: dict) -> str | None:
    env = content.get("environment") if isinstance(content.get("environment"), dict) else {}
    value = env.get("reference_digest") or content.get("reference_digest")
    if isinstance(value, str) and _DIGEST.fullmatch(value):
        return value
    return None


async def _bundle_digest(db: AsyncSession, task_id: uuid.UUID, attempt_id: uuid.UUID) -> str | None:
    path = await _bundle_for_attempt(db, task_id, attempt_id)
    if not path:
        return None
    from pathlib import Path

    file = Path(path)
    if not file.is_file():
        return None
    hexpart, _size = _hash_file(file)
    return f"sha256:{hexpart}"


async def _record_verifier(db: AsyncSession, stage: _Stage, bundle_digest: str | None) -> None:
    seen = await _challenger_seen(db, stage.task, stage.contract)
    content = stage.contract.content
    fixtures = content.get("fixtures_digest")
    observers = content.get("observers_digest")
    if isinstance(fixtures, str):
        seen.append(f"fixtures:{fixtures}")
    if isinstance(observers, str):
        seen.append(f"observers:{observers}")
    if bundle_digest:
        seen.append(f"bundle:{bundle_digest}")
    await _record_participant(
        db,
        stage.task,
        role=ParticipantRole.VERIFIER.value,
        operator_id=PLATFORM_VERIFIER,
        artifacts_seen=seen,
        platform_operated=True,
    )


async def _sync_task(db: AsyncSession, stage: _Stage) -> None:
    task = stage.task
    if task.state in TERMINAL_TASK_STATES:
        return
    claims = await _claims_for_task(db, task.id)
    if any(
        row.state in {ClaimState.SUBMITTED.value, ClaimState.VALIDATING.value} for row in claims
    ):
        return
    if any(row.state == ClaimState.ADJUDICATING.value for row in claims):
        if task.state == ChallengeTaskState.VALIDATING.value:
            await transition(
                db, task, ChallengeTaskState.ADJUDICATING.value, _reason("adjudication required")
            )
        return
    kinds = []
    for claim in claims:
        verdict = await _latest_verdict(db, claim.id)
        if verdict is not None:
            kinds.append(verdict.verdict)
    if any(kind == VerdictKind.CONFIRMED.value for kind in kinds):
        destination = ChallengeTaskState.CLOSED.value
        task_verdict = VerdictKind.CONFIRMED.value
        reason = "challenge decided"
    elif any(kind == VerdictKind.INCONCLUSIVE.value for kind in kinds):
        destination = ChallengeTaskState.INCONCLUSIVE.value
        task_verdict = VerdictKind.INCONCLUSIVE.value
        reason = "inconclusive"
    elif kinds and all(kind == VerdictKind.INVALID_OR_DUPLICATE.value for kind in kinds):
        destination = ChallengeTaskState.CLOSED.value
        task_verdict = VerdictKind.INVALID_OR_DUPLICATE.value
        reason = "no valid claim"
    else:
        destination = ChallengeTaskState.CLOSED.value
        task_verdict = VerdictKind.NO_FAILURE_FOUND.value
        reason = "challenge decided"
    if task.state == ChallengeTaskState.VALIDATING.value:
        await transition(db, task, destination, _reason(reason))
    elif (
        task.state == ChallengeTaskState.ADJUDICATING.value
        and destination in TASK_TRANSITIONS[ChallengeTaskState.ADJUDICATING.value]
    ):
        await transition(db, task, destination, _reason(reason))
    else:
        return
    await _append_task_verdict(db, stage, task_verdict, reason)
    await _enqueue(
        db,
        kind=JobKind.CHALLENGE_REPORT.value,
        task=task,
        contract=stage.contract,
        stage="report",
    )


async def _apply_outcome(db: AsyncSession, stage: _Stage, outcome) -> None:
    verdict = getattr(outcome, "verdict", None)
    if verdict is None:
        await _transition_claim(db, stage.claim, ClaimState.ADJUDICATING.value)
        await _reconcile(db, stage.reservation)
        await _sync_task(db, stage)
        return
    evidence = getattr(outcome, "evidence", {}) or {}
    await _append_verdict(
        db,
        task=stage.task,
        contract=stage.contract,
        claim=stage.claim,
        verdict=verdict,
        decided_by=DecidedBy.MACHINE.value,
        reason=getattr(outcome, "reason", None) or "verifier decided",
        evidence=evidence,
    )
    await _transition_claim(db, stage.claim, ClaimState.DECIDED.value)
    await _reconcile(db, stage.reservation)
    await _sync_task(db, stage)


async def _handle_validate(db: AsyncSession, job: GitHubJob) -> None:
    stage = await _open_stage(db, job, expected=None)
    if stage is None:
        return
    if stage.task.state != ChallengeTaskState.VALIDATING.value:
        return
    claim_id = _uuid(stage.payload.get("claim_id"), required=True)
    claim = await _one(db, ChallengeClaim, claim_id)
    if claim is None or claim.task_id != stage.task.id:
        raise PermanentJobError("challenge claim not found")
    stage.claim = claim
    if claim.state != ClaimState.VALIDATING.value:
        return
    if not _observers_for(stage.contract.content, claim.failure_class):
        await _record_verifier(db, stage, None)
        await _transition_claim(db, claim, ClaimState.ADJUDICATING.value)
        await _sync_task(db, stage)
        return
    claim_in = _claim_in(claim)
    try:
        bundle = await _bundle_digest(db, stage.task.id, claim.attempt_id)
        await _record_verifier(db, stage, bundle)
        stage.reservation = await _reserve(
            db,
            stage.task,
            f"challenge_validate:{claim.id}",
            stage.contract.version,
        )
        attempt = await _one(db, ChallengeAttempt, claim.attempt_id)
        if attempt is not None and attempt.reservation_id is None:
            attempt.reservation_id = stage.reservation.id
        target = await _source_or_stop(db, stage.task.id, "target")
        base = await _source_or_stop(db, stage.task.id, "base")
        reference = _reference_digest(stage.contract.content)
        results: dict[str, RunnerResult] = {}
        for purpose in _validation_purposes(stage.contract.content):
            if reservation_exhausted(stage.reservation):
                raise _AttemptStopped("budget exhausted")
            if purpose in {"base"}:
                source = base
                role = "base"
            elif purpose == "reference":
                source = reference or target
                role = "target"
            else:
                source = target
                role = "target"
            wheel = await _wheelhouse(db, stage.task.id, role)
            results[purpose] = await _run_purpose(
                db,
                stage,
                purpose,
                source,
                claim=claim_in,
                bundle_digest=bundle,
                wheelhouse=wheel,
            )
        outcome = challenge_validity.decide(
            claim=claim_in,
            contract_content=stage.contract.content,
            tamper_clean=True,
            target=results["target"],
            positive=results["positive"],
            negative=results["negative"],
            base=results["base"],
            reference=results.get("reference"),
            rederivation=results["rederive"],
            reruns=[results["rerun-1"], results["rerun-2"], results["rerun-3"]],
            minimized=results["minimized"],
        )
        await _apply_outcome(db, stage, outcome)
    except _AttemptStopped as stopped:
        await _stop_task(db, stage, stopped.reason)
    except RoleConflict:
        await _stop_task(db, stage, "role conflict")


async def _handle_report(db: AsyncSession, job: GitHubJob) -> None:
    stage = await _open_stage(db, job, expected=None)
    if stage is None:
        return
    if stage.task.state not in TERMINAL_TASK_STATES:
        return
    from app.services import challenge_report

    try:
        await challenge_report.build_report(db, stage.task.id)
    except challenge_report.ReportNotBuilt as exc:
        raise PermanentJobError("challenge report could not be built") from exc


_HANDLERS = {
    JobKind.CHALLENGE_FETCH.value: _handle_fetch,
    JobKind.CHALLENGE_DEPS.value: _handle_deps,
    JobKind.CHALLENGE_BASELINE.value: _handle_baseline,
    JobKind.CHALLENGE_ATTEMPT.value: _handle_attempt,
    JobKind.CHALLENGE_VALIDATE.value: _handle_validate,
    JobKind.CHALLENGE_REPORT.value: _handle_report,
}


async def handle_challenge_job(db: AsyncSession, job: GitHubJob) -> None:
    """Dispatch for JobKind.CHALLENGE_* kinds."""
    if not settings.CHALLENGE_ENGINE_ENABLED:
        return
    handler = _HANDLERS.get(_kind(job))
    if handler is None:
        raise PermanentJobError("unsupported challenge job")
    await handler(db, job)


async def _task_reservations(db: AsyncSession, task: ChallengeTask) -> list[SpendReservation]:
    attempts = (
        (await db.execute(select(ChallengeAttempt).where(ChallengeAttempt.task_id == task.id)))
        .scalars()
        .all()
    )
    claims = await _claims_for_task(db, task.id)
    subjects = [f"challenge_baseline:{task.id}"]
    subjects.extend(f"challenge_attempt:{row.id}" for row in attempts)
    subjects.extend(f"challenge_validate:{row.id}" for row in claims)
    stmt = select(SpendReservation).where(SpendReservation.subject.in_(subjects))
    return list((await db.execute(stmt)).scalars().all())


async def cancel_task(db: AsyncSession, task_id: uuid.UUID, reason: str) -> ChallengeTask:
    task = await _require_task(db, task_id)
    if ChallengeTaskState.CANCELLED.value not in TASK_TRANSITIONS.get(task.state, frozenset()):
        raise InvalidTransition(f"task cannot move from {task.state} to cancelled")
    await cancel_jobs(db, dedupe_key_prefix=f"chal:{task.id}:")
    for reservation in await _task_reservations(db, task):
        if reservation.status == ReservationStatus.RESERVED.value:
            await release_reservation(db, reservation.id)
    attempts = (
        (await db.execute(select(ChallengeAttempt).where(ChallengeAttempt.task_id == task.id)))
        .scalars()
        .all()
    )
    for attempt in attempts:
        if attempt.state in {AttemptState.QUEUED.value, AttemptState.RUNNING.value}:
            attempt.state = AttemptState.CANCELLED.value
            attempt.finished_at = _now()
            attempt.error = "cancelled"
    return await transition(db, task, ChallengeTaskState.CANCELLED.value, _reason(reason))


async def _record_adjudication(
    db: AsyncSession,
    *,
    task_id: uuid.UUID,
    claim_id: uuid.UUID,
    operator_id: str,
    verdict: str,
    reason: str,
) -> ChallengeVerdict:
    _reason(reason)
    task = await _require_task(db, task_id)
    claim = await _one(db, ChallengeClaim, claim_id)
    if claim is None or claim.task_id != task.id:
        raise LookupError("challenge claim not found")
    contract = await _one(db, ChallengeContract, claim.contract_id)
    if contract is None:
        raise LookupError("challenge contract not found")
    if verdict not in {item.value for item in VerdictKind}:
        raise InvalidTransition(f"claim cannot move from {claim.state} to decided")
    await _record_participant(
        db,
        task,
        role=ParticipantRole.ADJUDICATOR.value,
        operator_id=operator_id,
        artifacts_seen=[f"contract:{contract.id}"],
    )
    adjudicator_user_id = None
    try:
        adjudicator_user_id = uuid.UUID(str(operator_id))
    except ValueError:
        adjudicator_user_id = None
    row = await _append_verdict(
        db,
        task=task,
        contract=contract,
        claim=claim,
        verdict=verdict,
        decided_by=DecidedBy.ADJUDICATOR.value,
        reason=reason,
        evidence=_early_evidence(
            citation_valid=verdict != VerdictKind.INVALID_OR_DUPLICATE.value,
            tamper_clean=True,
            execution_ids=[],
        ),
        adjudicator_user_id=adjudicator_user_id,
        allow_duplicate=True,
    )
    await _transition_claim(db, claim, ClaimState.DECIDED.value)
    stage = _Stage(task=task, contract=contract, payload={}, claim=claim)
    await _sync_task(db, stage)
    return row
