"""Signed-in ATE ledger. Mounted later at /api/v1."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.services import ledger as ledger_service

router = APIRouter()


@router.get("/me/ledger")
async def read_ledger(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await ledger_service.get_ledger(db, user)


@router.get("/me/ledger.csv")
async def read_ledger_csv(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    payload = await ledger_service.get_ledger(db, user)
    return Response(
        content=ledger_service.ledger_csv(payload),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ledger.csv"},
    )
