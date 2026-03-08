from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bounty import Bounty, BountyStatus
from app.models.claim import Claim, ClaimStatus


async def count_active_claims(db: AsyncSession, bounty_id: uuid.UUID) -> int:
    q = (
        select(func.count())
        .select_from(Claim)
        .where(Claim.bounty_id == bounty_id, Claim.status == ClaimStatus.ACTIVE)
    )
    return (await db.execute(q)).scalar() or 0


async def create_claim(
    db: AsyncSession,
    *,
    bounty_id: uuid.UUID,
    agent_user_id: uuid.UUID,
    agent_exchange_bot_id: str,
) -> Claim:
    claim = Claim(
        bounty_id=bounty_id,
        agent_user_id=agent_user_id,
        agent_exchange_bot_id=agent_exchange_bot_id,
    )
    db.add(claim)
    await db.flush()
    return claim


async def get_claim(db: AsyncSession, claim_id: uuid.UUID) -> Claim | None:
    return (await db.execute(select(Claim).where(Claim.id == claim_id))).scalar_one_or_none()


async def abandon_claim(db: AsyncSession, claim: Claim, reason: str | None = None) -> Claim:
    claim.status = ClaimStatus.ABANDONED
    claim.abandon_reason = reason
    claim.resolved_at = datetime.now(timezone.utc)
    await db.flush()

    active = await count_active_claims(db, claim.bounty_id)
    if active == 0:
        bounty = (
            await db.execute(select(Bounty).where(Bounty.id == claim.bounty_id))
        ).scalar_one_or_none()
        if bounty and bounty.status == BountyStatus.CLAIMED:
            bounty.status = BountyStatus.OPEN
            await db.flush()

    return claim


async def user_claimed_bounties(
    db: AsyncSession, user_id: uuid.UUID
) -> list[Claim]:
    q = select(Claim).where(Claim.agent_user_id == user_id).order_by(Claim.claimed_at.desc())
    return list((await db.execute(q)).scalars().all())
