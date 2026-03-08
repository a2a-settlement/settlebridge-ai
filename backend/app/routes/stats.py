from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.bounty import Bounty, BountyStatus
from app.models.claim import Claim
from app.models.user import User, UserType

router = APIRouter()


@router.get("")
async def platform_stats(db: AsyncSession = Depends(get_db)):
    open_q = (
        select(func.count())
        .select_from(Bounty)
        .where(Bounty.status == BountyStatus.OPEN)
    )
    completed_q = (
        select(func.count())
        .select_from(Bounty)
        .where(Bounty.status == BountyStatus.COMPLETED)
    )
    total_settled_q = (
        select(func.coalesce(func.sum(Bounty.reward_amount), 0))
        .where(Bounty.status == BountyStatus.COMPLETED)
    )
    agents_q = (
        select(func.count())
        .select_from(User)
        .where(User.user_type.in_([UserType.AGENT_OPERATOR, UserType.BOTH]))
    )

    open_count = (await db.execute(open_q)).scalar() or 0
    completed_count = (await db.execute(completed_q)).scalar() or 0
    total_settled = (await db.execute(total_settled_q)).scalar() or 0
    agent_count = (await db.execute(agents_q)).scalar() or 0

    return {
        "open_bounties": open_count,
        "completed_bounties": completed_count,
        "total_settled_ate": total_settled,
        "active_agents": agent_count,
    }
