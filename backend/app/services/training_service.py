"""Business logic for the self-improving agent training loop."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bounty import Bounty
from app.models.claim import Claim
from app.models.submission import Submission, SubmissionStatus
from app.models.training_run import TrainingRun, TrainingRunStatus, TrainingTranscript
from app.models.score_history import ScoreHistory, ScoreMode
from app.services.review_service import QUALITY_PROMPT_VERSION, REVIEW_MODEL, prompt_hash
from app.services.score_history_write import score_history_diagnostics
from app.services.training_gates import apply_completion_resolution

_EMA_LAMBDA = 0.1
_ITER_STAKE_DEFAULT = 100  # ATE per iteration when budget is not further subdivided


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def compute_ema(scores: list[float], lam: float = _EMA_LAMBDA) -> float:
    """Exponential moving average with decay λ (same formula as exchange reputation).

    EMA_t = λ * score_t + (1 - λ) * EMA_{t-1}
    Seed with the first score so that a single-element list returns that score.
    """
    if not scores:
        return 0.0
    ema = scores[0]
    for s in scores[1:]:
        ema = lam * s + (1 - lam) * ema
    return ema


def build_merkle_root(provenance_hashes: list[str]) -> str:
    """Binary Merkle tree over SHA-256 provenance hashes.

    Each leaf is already a hex SHA-256 string.  Internal nodes hash the
    concatenation of two child hex strings.  An odd number of leaves duplicates
    the last one, matching the Bitcoin convention.
    """
    if not provenance_hashes:
        return hashlib.sha256(b"").hexdigest()

    layer = [h.encode() if isinstance(h, str) else h for h in provenance_hashes]

    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        layer = [
            hashlib.sha256(layer[i] + layer[i + 1]).digest()
            for i in range(0, len(layer), 2)
        ]

    return layer[0].hex() if isinstance(layer[0], bytes) else layer[0]


def _provenance_hash_for(submission: Submission) -> str:
    """Derive a SHA-256 provenance hash for a submission's deliverable."""
    payload = json.dumps(submission.deliverable or {}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Training run CRUD
# ---------------------------------------------------------------------------

async def create_run(
    db: AsyncSession,
    *,
    agent_user_id: uuid.UUID,
    bounty_id: uuid.UUID,
    max_iterations: int,
    stake_budget: int,
    score_threshold: float,
    task_type: str,
) -> TrainingRun:
    bounty = (await db.execute(select(Bounty).where(Bounty.id == bounty_id))).scalar_one_or_none()
    if bounty is None:
        raise ValueError(f"Bounty {bounty_id} not found")

    run = TrainingRun(
        bounty_id=bounty_id,
        agent_user_id=agent_user_id,
        status=TrainingRunStatus.RUNNING,
        max_iterations=max_iterations,
        stake_budget=stake_budget,
        stake_spent=0,
        score_threshold=score_threshold,
        task_type=task_type,
        bounty_snapshot={
            "title": bounty.title,
            "description": bounty.description,
            "acceptance_criteria": bounty.acceptance_criteria,
            "difficulty": bounty.difficulty.value if bounty.difficulty else None,
            "reward_amount": bounty.reward_amount,
        },
        iterations_completed=0,
    )
    db.add(run)
    await db.flush()
    return run


async def get_run(db: AsyncSession, run_id: uuid.UUID) -> TrainingRun | None:
    return (
        await db.execute(select(TrainingRun).where(TrainingRun.id == run_id))
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Score recording
# ---------------------------------------------------------------------------

async def record_score(
    db: AsyncSession,
    *,
    run: TrainingRun,
    submission: Submission,
    mediator_result: dict[str, Any],
    iter_stake: int = _ITER_STAKE_DEFAULT,
) -> ScoreHistory:
    """Write one ScoreHistory row and update run accounting."""
    confidence: float = float(mediator_result.get("confidence", 0.0))
    reasoning: str | None = mediator_result.get("reasoning")
    submission_review = submission.ai_review if isinstance(submission.ai_review, dict) else None
    diagnostics = score_history_diagnostics(
        mediator_result,
        submission_id=str(submission.id),
        task_type=run.task_type,
        ai_review=submission_review,
        compliance=submission.compliance if isinstance(submission.compliance, dict) else None,
    )

    prov_hash = _provenance_hash_for(submission)
    review_meta = submission_review or {}

    row = ScoreHistory(
        agent_user_id=run.agent_user_id,
        bounty_id=run.bounty_id,
        training_run_id=run.id,
        task_type=run.task_type,
        numeric_score=confidence,
        reasoning=reasoning,
        diagnostics=diagnostics,
        mode=ScoreMode.TRAINING,
        provenance_hash=prov_hash,
        judge_model=review_meta.get("model") or REVIEW_MODEL,
        quality_prompt_version=review_meta.get("quality_prompt_version") or QUALITY_PROMPT_VERSION,
        prompt_hash=review_meta.get("prompt_hash") or prompt_hash(),
    )
    db.add(row)

    run.iterations_completed += 1
    run.stake_spent += iter_stake

    await db.flush()
    return row


# ---------------------------------------------------------------------------
# Score history query
# ---------------------------------------------------------------------------

async def get_score_history(
    db: AsyncSession,
    *,
    training_run_id: uuid.UUID | None = None,
    agent_user_id: uuid.UUID | None = None,
    mode: str | None = None,
    task_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ScoreHistory]:
    q = select(ScoreHistory).order_by(ScoreHistory.created_at.asc())

    if training_run_id is not None:
        q = q.where(ScoreHistory.training_run_id == training_run_id)
    if agent_user_id is not None:
        q = q.where(ScoreHistory.agent_user_id == agent_user_id)
    if mode is not None:
        q = q.where(ScoreHistory.mode == ScoreMode(mode.upper()))
    if task_type is not None:
        q = q.where(ScoreHistory.task_type == task_type)

    q = q.limit(limit).offset(offset)
    return list((await db.execute(q)).scalars().all())


# ---------------------------------------------------------------------------
# Transcript generation
# ---------------------------------------------------------------------------

async def complete_run(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    agent_user_id: uuid.UUID,
) -> TrainingTranscript:
    """Finalise a training run: compute EMA, build Merkle root, persist transcript."""
    run = await get_run(db, run_id)
    if run is None:
        raise ValueError(f"TrainingRun {run_id} not found")
    if run.agent_user_id != agent_user_id:
        raise PermissionError("Not your training run")
    if run.status == TrainingRunStatus.COMPLETED:
        # Idempotent — return existing transcript
        existing = (
            await db.execute(
                select(TrainingTranscript).where(TrainingTranscript.training_run_id == run_id)
            )
        ).scalar_one_or_none()
        if existing:
            return existing

    history = await get_score_history(db, training_run_id=run_id)

    scores = [row.numeric_score for row in history]
    ema = compute_ema(scores)
    hashes = [row.provenance_hash for row in history if row.provenance_hash]
    merkle_root = build_merkle_root(hashes)

    # Fetch agent display id from user
    from app.models.user import User
    user = (await db.execute(select(User).where(User.id == agent_user_id))).scalar_one_or_none()
    agent_display_id = (user.exchange_bot_id or str(agent_user_id)) if user else str(agent_user_id)

    attempts = [
        {
            "iteration": i + 1,
            "numeric_score": row.numeric_score,
            "reasoning": row.reasoning,
            "diagnostics": row.diagnostics or {},
            "provenance_hash": row.provenance_hash,
            "timestamp": row.created_at.isoformat() if row.created_at else None,
        }
        for i, row in enumerate(history)
    ]

    signed_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "score_trajectory": scores,
        "attempts": attempts,
        "merkle_root": merkle_root,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    transcript = TrainingTranscript(
        training_run_id=run_id,
        agent_display_id=agent_display_id,
        bounty_id=run.bounty_id,
        total_iterations=run.iterations_completed,
        total_stake_spent=run.stake_spent,
        final_training_ema=ema,
        merkle_root=merkle_root,
        signed_payload=signed_payload,
    )
    db.add(transcript)

    run.status = TrainingRunStatus.COMPLETED
    run.completed_at = datetime.now(timezone.utc)

    # Auto-publish completed runs so they appear in the public feed.
    # Owners can opt out later via POST /training/runs/{id}/unpublish.
    run.public = True
    if not run.public_title:
        run.public_title = (run.bounty_snapshot or {}).get("title") or "Training Run"

    # Settle pending submissions only when the requester opted into auto-approve.
    # Winner must also pass recommendation, score_threshold, and compliance.
    # Tie-break is submitted_at ASC so an earlier equal score matches the
    # harness client's strict `>` (first kept iteration wins ties).
    from app.models.bounty import BountyStatus
    parent_bounty = (
        await db.execute(select(Bounty).where(Bounty.id == run.bounty_id))
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    auto_approve = bool(parent_bounty.auto_approve) if parent_bounty is not None else False

    pending_q = (
        select(Submission)
        .join(Claim, Claim.id == Submission.claim_id)
        .where(Claim.training_run_id == run_id)
        .where(Submission.status == SubmissionStatus.PENDING_REVIEW)
        .where(Submission.ai_review.isnot(None))
        .order_by(
            func.cast(Submission.ai_review["score"].as_string(), Integer).desc(),
            Submission.submitted_at.asc(),
        )
    )
    pending = list((await db.execute(pending_q)).scalars().all())

    if auto_approve:
        for i, sub in enumerate(pending):
            claim = (await db.execute(select(Claim).where(Claim.id == sub.claim_id))).scalar_one()
            apply_completion_resolution(
                sub,
                claim,
                is_winner=(i == 0),
                auto_approve=True,
                score_threshold=run.score_threshold,
                now=now,
            )

    # Seal the parent bounty as COMPLETED.
    # The training submission path intentionally resets the bounty to OPEN after
    # each iteration so the harness can re-claim.  This means the bounty is always
    # left in OPEN state when the run finishes — seal it here so the public
    # bounty detail page shows submissions and the bounty is no longer claimable.
    if parent_bounty is not None and parent_bounty.status != BountyStatus.COMPLETED:
        parent_bounty.status = BountyStatus.COMPLETED
        parent_bounty.completed_at = now

    await db.flush()
    return transcript
