"""Service layer for training runs and score history.

Implements the self-improving agent loop: operators create a training run
against a bounty, each iteration writes a ScoreHistory row, and when the
run completes a TrainingTranscript is generated with a Merkle-anchored
provenance chain over all attempts.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bounty import Bounty
from app.models.score_history import BountyMode, ScoreHistory
from app.models.training_run import TrainingRun, TrainingRunStatus, TrainingTranscript
from app.models.user import User

logger = logging.getLogger(__name__)

# Matches apply_ema_update in a2a-settlement/exchange/federation/reputation.py
_EMA_LAMBDA = 0.1


# ---------------------------------------------------------------------------
# Merkle helpers (self-contained — no external dependency)
# ---------------------------------------------------------------------------


def _sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _merkle_root(leaves: list[str]) -> str | None:
    """Compute a simple binary Merkle root over a list of hex leaf hashes."""
    if not leaves:
        return None
    nodes = list(leaves)
    while len(nodes) > 1:
        if len(nodes) % 2 == 1:
            nodes.append(nodes[-1])  # duplicate last leaf if odd count
        nodes = [
            _sha256(nodes[i] + nodes[i + 1]) for i in range(0, len(nodes), 2)
        ]
    return nodes[0]


# ---------------------------------------------------------------------------
# EMA helpers
# ---------------------------------------------------------------------------


def _apply_ema(current: float, outcome: float, lam: float = _EMA_LAMBDA) -> float:
    """Single EMA step — matches apply_ema_update on the exchange."""
    return current * (1.0 - lam) + outcome * lam


def _compute_ema_sequence(scores: list[float], initial: float = 0.5) -> float:
    """Run EMA over an ordered score sequence and return the final value."""
    ema = initial
    for s in scores:
        ema = _apply_ema(ema, s)
    return ema


# ---------------------------------------------------------------------------
# init_training_run
# ---------------------------------------------------------------------------


async def init_training_run(
    db: AsyncSession,
    agent_user: User,
    bounty_id: uuid.UUID,
    max_iterations: int,
    stake_budget: int,
    score_threshold: float,
    task_type: str | None = None,
) -> TrainingRun:
    """Create a new training run against a bounty.

    Snapshots the bounty's acceptance_criteria into the run record so that
    concurrent training runs against the same bounty each hold their own
    isolated copy.  The Bounty record is not modified.

    Does NOT create an escrow — the first escrow is created when the agent
    claims the bounty on iteration 1.
    """
    result = await db.execute(select(Bounty).where(Bounty.id == bounty_id))
    bounty = result.scalar_one_or_none()
    if not bounty:
        raise ValueError(f"Bounty {bounty_id} not found")

    bounty_snapshot: dict[str, Any] = {
        "bounty_id": str(bounty.id),
        "title": bounty.title,
        "description": bounty.description,
        "acceptance_criteria": bounty.acceptance_criteria or {},
        "task_type": task_type or bounty.tags[0] if bounty.tags else None,
        "provenance_tier": bounty.provenance_tier.value if bounty.provenance_tier else None,
        "snapshotted_at": datetime.now(timezone.utc).isoformat(),
    }

    run = TrainingRun(
        agent_id=agent_user.exchange_bot_id or str(agent_user.id),
        target_bounty_id=bounty_id,
        task_type=task_type,
        max_iterations=max_iterations,
        stake_budget=stake_budget,
        stake_spent=0,
        score_threshold=score_threshold,
        status=TrainingRunStatus.RUNNING,
        bounty_snapshot=bounty_snapshot,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.flush()
    logger.info(
        "Training run %s created for agent %s against bounty %s",
        run.id,
        run.agent_id,
        bounty_id,
    )
    return run


# ---------------------------------------------------------------------------
# record_score
# ---------------------------------------------------------------------------


async def record_score(
    db: AsyncSession,
    *,
    agent_id: str,
    bounty_id: uuid.UUID,
    task_type: str | None,
    numeric_score: float,
    reasoning: str | None,
    diagnostics: dict | None,
    mode: BountyMode,
    training_run_id: uuid.UUID | None = None,
    provenance: dict | None = None,
    iteration_stake: int = 0,
) -> ScoreHistory:
    """Write a single scored attempt to the score_history ledger.

    For training runs, also increments TrainingRun.stake_spent and
    transitions the run to EXHAUSTED if stake_budget would be exceeded
    after this iteration.
    """
    provenance_hash = (
        _sha256(str(sorted(provenance.items()))) if provenance else None
    )

    row = ScoreHistory(
        agent_id=agent_id,
        bounty_id=bounty_id,
        task_type=task_type,
        numeric_score=max(0.0, min(1.0, numeric_score)),
        reasoning=reasoning,
        diagnostics=diagnostics,
        mode=mode,
        training_run_id=training_run_id,
        provenance_hash=provenance_hash,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(row)

    if training_run_id and iteration_stake:
        result = await db.execute(
            select(TrainingRun).where(TrainingRun.id == training_run_id)
        )
        run = result.scalar_one_or_none()
        if run:
            run.stake_spent = (run.stake_spent or 0) + iteration_stake
            if run.stake_spent >= run.stake_budget and run.status == TrainingRunStatus.RUNNING:
                run.status = TrainingRunStatus.EXHAUSTED
                logger.info("Training run %s budget exhausted", run.id)

    await db.flush()
    return row


# ---------------------------------------------------------------------------
# generate_transcript
# ---------------------------------------------------------------------------


async def generate_transcript(db: AsyncSession, run_id: uuid.UUID) -> TrainingTranscript:
    """Generate and persist an immutable TrainingTranscript for a completed run.

    - Fetches all ScoreHistory rows for the run ordered by timestamp.
    - Computes training EMA using λ=0.1 (same as exchange production EMA).
    - Builds a Merkle root over the provenance_hash column.
    - Writes a TrainingTranscript row (unique per run — fails if already exists).
    """
    result = await db.execute(
        select(TrainingRun).where(TrainingRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise ValueError(f"TrainingRun {run_id} not found")

    # Check no transcript already exists
    existing = await db.execute(
        select(TrainingTranscript).where(TrainingTranscript.training_run_id == run_id)
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"Transcript already exists for run {run_id}")

    # Fetch all score rows ordered by timestamp
    rows_result = await db.execute(
        select(ScoreHistory)
        .where(ScoreHistory.training_run_id == run_id)
        .order_by(ScoreHistory.timestamp)
    )
    rows = list(rows_result.scalars().all())

    # Compute training EMA (λ=0.1, initial=0.5, same formula as exchange)
    scores = [r.numeric_score for r in rows]
    training_ema = _compute_ema_sequence(scores) if scores else 0.5

    # Merkle root over provenance hashes (skip None hashes)
    leaf_hashes = [r.provenance_hash for r in rows if r.provenance_hash]
    merkle_root = _merkle_root(leaf_hashes)

    # Build the ordered attempt sequence for the signed payload
    attempts = [
        {
            "iteration": i + 1,
            "score_history_id": str(r.id),
            "numeric_score": r.numeric_score,
            "reasoning": r.reasoning,
            "diagnostics": r.diagnostics,
            "provenance_hash": r.provenance_hash,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        }
        for i, r in enumerate(rows)
    ]

    now = datetime.now(timezone.utc)

    signed_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "training_run_id": str(run_id),
        "agent_id": run.agent_id,
        "bounty_id": str(run.target_bounty_id),
        "task_type": run.task_type,
        "total_iterations": len(rows),
        "total_stake_spent": run.stake_spent or 0,
        "score_threshold": run.score_threshold,
        "final_training_ema": training_ema,
        "score_trajectory": scores,
        "merkle_root": merkle_root,
        "attempts": attempts,
        "generated_at": now.isoformat(),
    }

    transcript = TrainingTranscript(
        training_run_id=run_id,
        agent_id=run.agent_id,
        bounty_id=run.target_bounty_id,
        total_iterations=len(rows),
        total_stake_spent=run.stake_spent or 0,
        final_production_ema=None,  # fetched from exchange by caller if needed
        final_training_ema=training_ema,
        merkle_root=merkle_root,
        signed_payload=signed_payload,
        generated_at=now,
    )
    db.add(transcript)

    # Mark run as completed if not already exhausted
    if run.status == TrainingRunStatus.RUNNING:
        run.status = TrainingRunStatus.COMPLETED
    run.completed_at = now

    await db.flush()
    logger.info(
        "Transcript generated for training run %s: %d iterations, EMA=%.4f, merkle=%s",
        run_id,
        len(rows),
        training_ema,
        merkle_root,
    )
    return transcript
