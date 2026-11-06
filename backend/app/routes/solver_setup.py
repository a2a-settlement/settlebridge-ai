"""Solver setup status for the signed-in user."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.services.solver_setup import setup_status

router = APIRouter()


@router.get("/solver/setup-status")
async def solver_setup_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await setup_status(db, user)
