from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.bounty import BountyStatus
from app.models.notification import NotificationType
from app.models.user import User
from app.schemas.claim import AbandonRequest, ClaimResponse
from app.services import bounty_service, claim_service, exchange as exchange_svc
from app.services.notification_service import create_notification

router = APIRouter()


@router.post(
    "/bounties/{bounty_id}/claim",
    response_model=ClaimResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["claims"],
)
async def claim_bounty(
    bounty_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not user.exchange_bot_id:
        raise HTTPException(status_code=400, detail="Link your exchange account first")

    bounty = await bounty_service.get_bounty(db, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")
    if bounty.status != BountyStatus.OPEN:
        raise HTTPException(status_code=400, detail="Bounty is not open for claims")

    active_count = await claim_service.count_active_claims(db, bounty_id)
    if active_count >= bounty.max_claims:
        raise HTTPException(status_code=400, detail="Maximum claims reached")

    # Create escrow now that we know the provider (Option A)
    try:
        escrow_result = exchange_svc.create_escrow(
            user=await _get_requester(db, bounty.requester_id),
            provider_id=user.exchange_bot_id,
            amount=bounty.reward_amount,
            task_id=str(bounty.id),
            required_attestation_level=bounty.provenance_tier.value if bounty.provenance_tier else None,
            ttl_minutes=10080,  # 7 days
        )
        escrow_id = escrow_result.get("escrow_id", escrow_result.get("id", ""))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to create escrow: {exc}")

    bounty.escrow_id = escrow_id
    bounty.status = BountyStatus.CLAIMED

    claim = await claim_service.create_claim(
        db,
        bounty_id=bounty_id,
        agent_user_id=user.id,
        agent_exchange_bot_id=user.exchange_bot_id,
    )

    await create_notification(
        db,
        user_id=bounty.requester_id,
        type=NotificationType.BOUNTY_CLAIMED,
        title="Bounty Claimed",
        message=f'Your bounty "{bounty.title}" has been claimed.',
        reference_id=bounty.id,
    )

    await db.commit()
    await db.refresh(claim)
    return ClaimResponse.model_validate(claim)


async def _get_requester(db: AsyncSession, user_id: uuid.UUID) -> User:
    from sqlalchemy import select
    from app.models.user import User

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=500, detail="Requester not found")
    return user


@router.post("/claims/{claim_id}/abandon", response_model=ClaimResponse)
async def abandon_claim(
    claim_id: uuid.UUID,
    body: AbandonRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    claim = await claim_service.get_claim(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim.agent_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your claim")
    if claim.status.value != "active":
        raise HTTPException(status_code=400, detail="Can only abandon active claims")

    reason = body.reason if body else None
    claim = await claim_service.abandon_claim(db, claim, reason)

    bounty = await bounty_service.get_bounty(db, claim.bounty_id)
    if bounty:
        await create_notification(
            db,
            user_id=bounty.requester_id,
            type=NotificationType.CLAIM_ABANDONED,
            title="Claim Abandoned",
            message=f'An agent abandoned their claim on "{bounty.title}".',
            reference_id=bounty.id,
        )

    await db.commit()
    await db.refresh(claim)
    return ClaimResponse.model_validate(claim)


@router.get("/bounties/my/claimed", response_model=list[ClaimResponse])
async def my_claims(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await claim_service.user_claimed_bounties(db, user.id)
    return [ClaimResponse.model_validate(c) for c in rows]
