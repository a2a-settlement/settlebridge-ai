"""Training run management endpoints.

POST  /api/training/runs              — init a training run
GET   /api/training/runs/{run_id}     — status + iteration count
POST  /api/training/runs/{run_id}/complete  — trigger transcript generation
GET   /api/training/runs/{run_id}/transcript — return immutable transcript
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.training_run import TrainingRun, TrainingTranscript
from app.models.user import User
from app.services import training_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class InitTrainingRunRequest(BaseModel):
    bounty_id: uuid.UUID
    max_iterations: int = Field(ge=1, le=500)
    stake_budget: int = Field(ge=1, description="Total micro-stake budget in ATE for this run")
    score_threshold: float = Field(ge=0.0, le=1.0, description="Stop when score >= this value")
    task_type: str | None = None


class TrainingRunResponse(BaseModel):
    run_id: str
    agent_id: str
    target_bounty_id: str
    task_type: str | None
    max_iterations: int
    stake_budget: int
    stake_spent: int
    score_threshold: float
    status: str
    iteration_count: int
    started_at: str
    completed_at: str | None


class CompleteTrainingRunRequest(BaseModel):
    """Optional body — no fields required, but allows future extension."""
    pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def init_training_run(
    body: InitTrainingRunRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Initialise a training run against a bounty.

    Snapshots the bounty's acceptance criteria into the run.  Does not create
    an escrow — the harness creates one per iteration by claiming the bounty.
    """
    try:
        run = await training_service.init_training_run(
            db,
            agent_user=user,
            bounty_id=body.bounty_id,
            max_iterations=body.max_iterations,
            stake_budget=body.stake_budget,
            score_threshold=body.score_threshold,
            task_type=body.task_type,
        )
        await db.commit()
        await db.refresh(run)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"run_id": str(run.id), "status": run.status.value}


@router.get("/runs/{run_id}", response_model=TrainingRunResponse)
async def get_training_run(
    run_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return status and current iteration count for a training run."""
    result = await db.execute(select(TrainingRun).where(TrainingRun.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Training run not found")

    # Count scored iterations
    from sqlalchemy import func, select as sa_select
    from app.models.score_history import ScoreHistory

    count_result = await db.execute(
        sa_select(func.count()).where(ScoreHistory.training_run_id == run_id)
    )
    iteration_count = count_result.scalar() or 0

    return TrainingRunResponse(
        run_id=str(run.id),
        agent_id=run.agent_id,
        target_bounty_id=str(run.target_bounty_id),
        task_type=run.task_type,
        max_iterations=run.max_iterations,
        stake_budget=run.stake_budget,
        stake_spent=run.stake_spent or 0,
        score_threshold=run.score_threshold,
        status=run.status.value,
        iteration_count=iteration_count,
        started_at=run.started_at.isoformat() if run.started_at else "",
        completed_at=run.completed_at.isoformat() if run.completed_at else None,
    )


@router.post("/runs/{run_id}/complete", status_code=status.HTTP_201_CREATED)
async def complete_training_run(
    run_id: uuid.UUID,
    _body: CompleteTrainingRunRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger transcript generation for a training run.

    Idempotency: returns 409 if a transcript already exists for this run.
    The generated transcript is immutable — it cannot be modified or deleted.
    """
    try:
        transcript = await training_service.generate_transcript(db, run_id)
        await db.commit()
        await db.refresh(transcript)
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=msg)
        if "already exists" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)

    return {
        "transcript_id": str(transcript.id),
        "run_id": str(run_id),
        "total_iterations": transcript.total_iterations,
        "final_training_ema": transcript.final_training_ema,
        "merkle_root": transcript.merkle_root,
        "generated_at": transcript.generated_at.isoformat() if transcript.generated_at else None,
    }


@router.get("/runs/{run_id}/transcript")
async def get_transcript(
    run_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the immutable training transcript for a completed run."""
    result = await db.execute(
        select(TrainingTranscript).where(TrainingTranscript.training_run_id == run_id)
    )
    transcript = result.scalar_one_or_none()
    if not transcript:
        raise HTTPException(
            status_code=404,
            detail="Transcript not found — call POST /complete first",
        )

    return {
        "id": str(transcript.id),
        "training_run_id": str(transcript.training_run_id),
        "agent_id": transcript.agent_id,
        "bounty_id": str(transcript.bounty_id),
        "total_iterations": transcript.total_iterations,
        "total_stake_spent": transcript.total_stake_spent,
        "final_production_ema": transcript.final_production_ema,
        "final_training_ema": transcript.final_training_ema,
        "merkle_root": transcript.merkle_root,
        "signed_payload": transcript.signed_payload,
        "generated_at": (
            transcript.generated_at.isoformat() if transcript.generated_at else None
        ),
    }
