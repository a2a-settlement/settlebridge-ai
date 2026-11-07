"""Signed-in user's own GitHub bounty leases. Mounted at /api/v1."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.challenge import MyLeasesOut
from app.services import github_my_leases

router = APIRouter()


@router.get("/github/leases/mine", response_model=MyLeasesOut)
async def list_my_leases(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MyLeasesOut:
    return await github_my_leases.list_my_leases(db, user.id)
