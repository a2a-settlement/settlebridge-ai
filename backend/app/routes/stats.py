from __future__ import annotations

import httpx

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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


@router.get("/economic-velocity")
async def economic_velocity(
    window_days: int = Query(default=30, ge=1, le=365),
):
    """Return economic velocity metrics separating arms-length from self-dealing volume.

    Fetches aggregate stats from the exchange. economic_velocity uses only
    arms-length transaction volume — the defensible headline metric.
    """
    base = settings.effective_exchange_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base}/v1/stats")
            resp.raise_for_status()
            exchange_stats = resp.json()
    except Exception:
        exchange_stats = {}

    total_volume = exchange_stats.get("total_volume", 0)
    arms_length_volume = exchange_stats.get("arms_length_volume", total_volume)
    circulating_supply = max(exchange_stats.get("circulating_supply", 1), 1)

    apparent_velocity = total_volume / circulating_supply
    economic_velocity = arms_length_volume / circulating_supply

    return {
        "window_days": window_days,
        "total_volume": total_volume,
        "arms_length_volume": arms_length_volume,
        "self_dealing_volume": total_volume - arms_length_volume,
        "apparent_velocity": round(apparent_velocity, 6),
        "economic_velocity": round(economic_velocity, 6),
    }
