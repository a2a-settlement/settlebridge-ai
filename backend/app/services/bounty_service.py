from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.bounty import Bounty, BountyStatus, Difficulty
from app.models.claim import Claim, ClaimStatus


async def create_bounty(db: AsyncSession, *, requester_id: uuid.UUID, **kwargs) -> Bounty:
    bounty = Bounty(requester_id=requester_id, status=BountyStatus.DRAFT, **kwargs)
    db.add(bounty)
    await db.flush()
    return bounty


async def get_bounty(db: AsyncSession, bounty_id: uuid.UUID) -> Bounty | None:
    q = (
        select(Bounty)
        .options(selectinload(Bounty.category), selectinload(Bounty.claims))
        .where(Bounty.id == bounty_id)
    )
    return (await db.execute(q)).scalar_one_or_none()


async def _batch_active_claims_counts(
    db: AsyncSession, bounty_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Return a mapping of bounty_id -> active claim count for the given ids."""
    if not bounty_ids:
        return {}
    count_q = (
        select(Claim.bounty_id, func.count().label("cnt"))
        .where(Claim.bounty_id.in_(bounty_ids), Claim.status == ClaimStatus.ACTIVE)
        .group_by(Claim.bounty_id)
    )
    result = await db.execute(count_q)
    return {row.bounty_id: row.cnt for row in result.all()}


async def list_bounties(
    db: AsyncSession,
    *,
    status: BountyStatus | None = None,
    category_id: uuid.UUID | None = None,
    difficulty: Difficulty | None = None,
    min_reward: int | None = None,
    max_reward: int | None = None,
    tags: list[str] | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[tuple[Bounty, int]], int]:
    q = select(Bounty).options(selectinload(Bounty.category))
    count_q = select(func.count()).select_from(Bounty)

    filters = []
    if status:
        filters.append(Bounty.status == status)
    if category_id:
        filters.append(Bounty.category_id == category_id)
    if difficulty:
        filters.append(Bounty.difficulty == difficulty)
    if min_reward is not None:
        filters.append(Bounty.reward_amount >= min_reward)
    if max_reward is not None:
        filters.append(Bounty.reward_amount <= max_reward)
    if tags:
        filters.append(Bounty.tags.overlap(tags))
    if search:
        pattern = f"%{search}%"
        filters.append(or_(Bounty.title.ilike(pattern), Bounty.description.ilike(pattern)))

    for f in filters:
        q = q.where(f)
        count_q = count_q.where(f)

    total = (await db.execute(count_q)).scalar() or 0
    q = q.order_by(Bounty.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = list((await db.execute(q)).scalars().all())

    counts = await _batch_active_claims_counts(db, [b.id for b in rows])
    return [(b, counts.get(b.id, 0)) for b in rows], total


async def update_bounty(db: AsyncSession, bounty: Bounty, **kwargs) -> Bounty:
    for key, val in kwargs.items():
        if val is not None:
            setattr(bounty, key, val)
    await db.flush()
    return bounty


async def fund_bounty(db: AsyncSession, bounty: Bounty, escrow_id: str) -> Bounty:
    bounty.escrow_id = escrow_id
    bounty.status = BountyStatus.OPEN
    bounty.funded_at = datetime.now(timezone.utc)
    await db.flush()
    return bounty


async def cancel_bounty(db: AsyncSession, bounty: Bounty) -> Bounty:
    bounty.status = BountyStatus.CANCELLED
    await db.flush()
    return bounty


async def complete_bounty(db: AsyncSession, bounty: Bounty) -> Bounty:
    bounty.status = BountyStatus.COMPLETED
    bounty.completed_at = datetime.now(timezone.utc)
    await db.flush()
    return bounty


async def user_posted_bounties(db: AsyncSession, user_id: uuid.UUID) -> list[tuple[Bounty, int]]:
    q = (
        select(Bounty)
        .options(selectinload(Bounty.category))
        .where(Bounty.requester_id == user_id)
        .order_by(Bounty.created_at.desc())
    )
    rows = list((await db.execute(q)).scalars().all())
    counts = await _batch_active_claims_counts(db, [b.id for b in rows])
    return [(b, counts.get(b.id, 0)) for b in rows]
