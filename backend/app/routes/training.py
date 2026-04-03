from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.training_run import TrainingRun, TrainingTranscript
from app.models.score_history import ScoreHistory
from app.models.user import User
from app.services import training_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CreateRunRequest(BaseModel):
    bounty_id: uuid.UUID
    max_iterations: int = 10
    stake_budget: int = 1000
    score_threshold: float = 0.85
    task_type: str


class TrainingRunResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    bounty_id: uuid.UUID
    max_iterations: int
    stake_budget: int
    stake_spent: int
    score_threshold: float
    task_type: str
    iterations_completed: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ScoreHistoryRow(BaseModel):
    id: uuid.UUID
    agent_user_id: uuid.UUID
    bounty_id: uuid.UUID
    training_run_id: uuid.UUID | None = None
    task_type: str | None = None
    numeric_score: float
    reasoning: str | None = None
    diagnostics: dict | None = None
    mode: str
    provenance_hash: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TranscriptResponse(BaseModel):
    id: uuid.UUID
    training_run_id: uuid.UUID
    agent_id: str
    bounty_id: uuid.UUID
    total_iterations: int
    total_stake_spent: int
    final_training_ema: float
    merkle_root: str
    signed_payload: dict[str, Any]
    generated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/training/runs",
    response_model=TrainingRunResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["training"],
)
async def create_training_run(
    body: CreateRunRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        run = await training_service.create_run(
            db,
            agent_user_id=user.id,
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

    return TrainingRunResponse(
        run_id=run.id,
        status=run.status.value,
        bounty_id=run.bounty_id,
        max_iterations=run.max_iterations,
        stake_budget=run.stake_budget,
        stake_spent=run.stake_spent,
        score_threshold=run.score_threshold,
        task_type=run.task_type,
        iterations_completed=run.iterations_completed,
        created_at=run.created_at,
    )


@router.post(
    "/training/runs/{run_id}/complete",
    response_model=TranscriptResponse,
    tags=["training"],
)
async def complete_training_run(
    run_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        transcript = await training_service.complete_run(
            db, run_id=run_id, agent_user_id=user.id
        )
        await db.commit()
        await db.refresh(transcript)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    return _transcript_response(transcript)


@router.get(
    "/training/runs/{run_id}/transcript",
    response_model=TranscriptResponse,
    tags=["training"],
)
async def get_training_transcript(
    run_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import select

    run = await training_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Training run not found")
    if run.agent_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your training run")

    transcript = (
        await db.execute(
            select(TrainingTranscript).where(TrainingTranscript.training_run_id == run_id)
        )
    ).scalar_one_or_none()

    if transcript is None:
        raise HTTPException(
            status_code=404,
            detail="Transcript not yet generated — POST /training/runs/{run_id}/complete first",
        )

    return _transcript_response(transcript)


@router.get(
    "/score-history",
    response_model=list[ScoreHistoryRow],
    tags=["training"],
)
async def list_score_history(
    training_run_id: uuid.UUID | None = Query(default=None),
    agent_id: str | None = Query(default=None, description="SettleBridge user UUID of the agent"),
    mode: str | None = Query(default=None, description="training or production"),
    task_type: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Callers can query by agent_id (UUID string) or implicitly for themselves
    agent_user_id: uuid.UUID | None = None
    if agent_id:
        try:
            agent_user_id = uuid.UUID(agent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="agent_id must be a valid UUID")
    elif training_run_id is None:
        # Default to the calling user's own history
        agent_user_id = user.id

    rows = await training_service.get_score_history(
        db,
        training_run_id=training_run_id,
        agent_user_id=agent_user_id,
        mode=mode,
        task_type=task_type,
        limit=limit,
        offset=offset,
    )
    return [_score_row_response(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _transcript_response(t: TrainingTranscript) -> TranscriptResponse:
    return TranscriptResponse(
        id=t.id,
        training_run_id=t.training_run_id,
        agent_id=t.agent_display_id,
        bounty_id=t.bounty_id,
        total_iterations=t.total_iterations,
        total_stake_spent=t.total_stake_spent,
        final_training_ema=t.final_training_ema,
        merkle_root=t.merkle_root,
        signed_payload=t.signed_payload,
        generated_at=t.generated_at,
    )


def _score_row_response(r: ScoreHistory) -> ScoreHistoryRow:
    return ScoreHistoryRow(
        id=r.id,
        agent_user_id=r.agent_user_id,
        bounty_id=r.bounty_id,
        training_run_id=r.training_run_id,
        task_type=r.task_type,
        numeric_score=r.numeric_score,
        reasoning=r.reasoning,
        diagnostics=r.diagnostics,
        mode=r.mode.value,
        provenance_hash=r.provenance_hash,
        created_at=r.created_at,
    )
