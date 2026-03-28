from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.bounty import BountyStatus, Difficulty, ProvenanceTier
from app.models.notification import NotificationType
from app.models.user import User
from app.schemas.bounty import (
    BountyCreateRequest,
    BountyListResponse,
    BountyResponse,
    BountyUpdateRequest,
)
from app.services import bounty_service, exchange as exchange_svc
from app.services.notification_service import create_notification

router = APIRouter()


@router.get("", response_model=BountyListResponse)
async def list_bounties(
    status_filter: BountyStatus | None = Query(None, alias="status"),
    category_id: uuid.UUID | None = None,
    difficulty: Difficulty | None = None,
    min_reward: int | None = None,
    max_reward: int | None = None,
    tags: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    rows, total = await bounty_service.list_bounties(
        db,
        status=status_filter,
        category_id=category_id,
        difficulty=difficulty,
        min_reward=min_reward,
        max_reward=max_reward,
        tags=tag_list,
        search=search,
        page=page,
        page_size=page_size,
    )
    return BountyListResponse(
        bounties=[BountyResponse.model_validate(b) for b in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/my/posted", response_model=list[BountyResponse])
async def my_posted(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await bounty_service.user_posted_bounties(db, user.id)
    return [BountyResponse.model_validate(b) for b in rows]


@router.get("/{bounty_id}", response_model=BountyResponse)
async def get_bounty(bounty_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    bounty = await bounty_service.get_bounty(db, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")
    return BountyResponse.model_validate(bounty)


@router.post("", response_model=BountyResponse, status_code=status.HTTP_201_CREATED)
async def create_bounty(
    body: BountyCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if body.deadline and body.deadline < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Deadline must be in the future")

    bounty = await bounty_service.create_bounty(
        db,
        requester_id=user.id,
        title=body.title,
        description=body.description,
        category_id=body.category_id,
        tags=body.tags,
        acceptance_criteria=body.acceptance_criteria.model_dump() if body.acceptance_criteria else None,
        reward_amount=body.reward_amount,
        deadline=body.deadline,
        max_claims=body.max_claims,
        min_reputation=body.min_reputation,
        difficulty=body.difficulty,
        auto_approve=body.auto_approve,
        provenance_tier=body.provenance_tier,
    )
    await db.commit()
    await db.refresh(bounty)
    return BountyResponse.model_validate(bounty)


@router.put("/{bounty_id}", response_model=BountyResponse)
async def update_bounty(
    bounty_id: uuid.UUID,
    body: BountyUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    bounty = await bounty_service.get_bounty(db, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")
    if bounty.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Not your bounty")
    if bounty.status != BountyStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Can only update draft bounties")

    updates = body.model_dump(exclude_none=True)
    if "acceptance_criteria" in updates and updates["acceptance_criteria"]:
        updates["acceptance_criteria"] = body.acceptance_criteria.model_dump()
    bounty = await bounty_service.update_bounty(db, bounty, **updates)
    await db.commit()
    await db.refresh(bounty)
    return BountyResponse.model_validate(bounty)


@router.post("/{bounty_id}/fund", response_model=BountyResponse)
async def fund_bounty(
    bounty_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    bounty = await bounty_service.get_bounty(db, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")
    if bounty.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Not your bounty")
    if bounty.status != BountyStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Bounty is not in draft status")

    escrow_id = "pending_claim"
    if user.exchange_bot_id:
        try:
            balance_data = exchange_svc.get_balance(user)
            balance = balance_data.get("available", balance_data.get("balance", 0))
        except Exception:
            raise HTTPException(status_code=502, detail="Failed to check exchange balance")

        if balance < bounty.reward_amount:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance: {balance} ATE (need {bounty.reward_amount})",
            )

    bounty = await bounty_service.fund_bounty(db, bounty, escrow_id=escrow_id)
    await db.commit()
    await db.refresh(bounty)
    return BountyResponse.model_validate(bounty)


@router.post("/{bounty_id}/cancel", response_model=BountyResponse)
async def cancel_bounty(
    bounty_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    bounty = await bounty_service.get_bounty(db, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")
    if bounty.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Not your bounty")
    if bounty.status not in (BountyStatus.DRAFT, BountyStatus.OPEN):
        raise HTTPException(status_code=400, detail="Cannot cancel bounty in current status")

    if bounty.escrow_id and bounty.escrow_id != "pending_claim":
        try:
            exchange_svc.refund_escrow(user, bounty.escrow_id, reason="Bounty cancelled")
        except Exception:
            raise HTTPException(status_code=502, detail="Failed to refund escrow")

    bounty = await bounty_service.cancel_bounty(db, bounty)
    await db.commit()
    await db.refresh(bounty)
    return BountyResponse.model_validate(bounty)
