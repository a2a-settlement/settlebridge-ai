"""Public SettleBridge Cyber tasks. Mounted at /api/v1."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.challenge import CyberTaskDetailOut
from app.services import cyber_tasks

router = APIRouter()


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


@router.get("/cyber/tasks")
async def list_cyber_tasks(
    view: Literal["available", "in_progress", "completed"] = "available",
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await cyber_tasks.list_tasks(db, view=view, page=page, page_size=page_size)


@router.get("/cyber/tasks/{bounty_id}", response_model=CyberTaskDetailOut)
async def get_cyber_task(
    bounty_id: str,
    db: AsyncSession = Depends(get_db),
) -> CyberTaskDetailOut:
    item = await cyber_tasks.get_task(db, bounty_id)
    if item is None:
        raise _not_found()
    return CyberTaskDetailOut.model_validate(item)
