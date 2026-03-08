from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.category import Category
from app.schemas.bounty import CategoryResponse

router = APIRouter()


@router.get("", response_model=list[CategoryResponse])
async def list_categories(db: AsyncSession = Depends(get_db)):
    q = select(Category).order_by(Category.sort_order)
    rows = (await db.execute(q)).scalars().all()
    return [CategoryResponse.model_validate(c) for c in rows]
