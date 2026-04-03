"""GET /api/score-history — queryable score ledger for agents."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.score_history import ScoreHistory, ScoreMode
from app.models.user import User

router = APIRouter()


@router.get("/score-history")
async def list_score_history(
    agent_id: str | None = Query(default=None, description="Exchange bot_id of the agent"),
    bounty_id: uuid.UUID | None = Query(default=None),
    task_type: str | None = Query(default=None),
    mode: ScoreMode | None = Query(default=None),
    training_run_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return an ordered sequence of scored attempts from the score ledger.

    All filter parameters are optional and combinable.  Results are ordered
    by timestamp ascending so the harness can reconstruct the score
    trajectory from a single call.
    """
    stmt = select(ScoreHistory).order_by(ScoreHistory.created_at)

    if agent_id is not None:
        stmt = stmt.where(ScoreHistory.agent_id == agent_id)
    if bounty_id is not None:
        stmt = stmt.where(ScoreHistory.bounty_id == bounty_id)
    if task_type is not None:
        stmt = stmt.where(ScoreHistory.task_type == task_type)
    if mode is not None:
        stmt = stmt.where(ScoreHistory.mode == mode)
    if training_run_id is not None:
        stmt = stmt.where(ScoreHistory.training_run_id == training_run_id)

    result = await db.execute(stmt.offset(offset).limit(limit))
    rows = result.scalars().all()

    return {
        "items": [
            {
                "id": str(r.id),
                "agent_id": r.agent_id,
                "bounty_id": str(r.bounty_id),
                "task_type": r.task_type,
                "numeric_score": r.numeric_score,
                "reasoning": r.reasoning,
                "diagnostics": r.diagnostics,
                "mode": r.mode.value,
                "training_run_id": str(r.training_run_id) if r.training_run_id else None,
                "provenance_hash": r.provenance_hash,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ],
        "count": len(rows),
        "offset": offset,
    }
