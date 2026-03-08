from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.notification import NotificationType
from app.models.submission import SubmissionStatus
from app.models.user import User
from app.schemas.submission import (
    DisputeRequest,
    EfficacyReviewRequest,
    ReviewRequest,
    ScoredApprovalRequest,
    SubmissionResponse,
    SubmitWorkRequest,
)
from app.services import (
    bounty_service,
    claim_service,
    exchange as exchange_svc,
    submission_service,
)
from app.services.notification_service import create_notification
from app.utils.helpers import compute_content_hash

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_requester(db: AsyncSession, user_id: uuid.UUID) -> User:
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=500, detail="Requester not found")
    return user


@router.post(
    "/claims/{claim_id}/submit",
    response_model=SubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["submissions"],
)
async def submit_work(
    claim_id: uuid.UUID,
    body: SubmitWorkRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    claim = await claim_service.get_claim(db, claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim.agent_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your claim")
    if claim.status.value != "active":
        raise HTTPException(status_code=400, detail="Claim is not in active status")

    bounty = await bounty_service.get_bounty(db, claim.bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    provenance_dict = body.provenance.model_dump() if body.provenance else None

    errors = submission_service.validate_provenance(provenance_dict, bounty.provenance_tier)
    if errors:
        raise HTTPException(status_code=400, detail={"provenance_errors": errors})

    # Call SDK deliver if escrow exists
    if bounty.escrow_id and bounty.escrow_id != "pending_claim":
        try:
            content_hash = compute_content_hash(body.deliverable.content)
            exchange_svc.deliver(
                user,
                escrow_id=bounty.escrow_id,
                content=body.deliverable.content,
                content_hash=content_hash,
                provenance=provenance_dict,
            )
        except Exception as exc:
            logger.warning("SDK deliver call failed (non-fatal): %s", exc)

    sub = await submission_service.create_submission(
        db,
        claim_id=claim.id,
        bounty_id=bounty.id,
        agent_user_id=user.id,
        deliverable=body.deliverable.model_dump(),
        provenance=provenance_dict,
    )

    await create_notification(
        db,
        user_id=bounty.requester_id,
        type=NotificationType.WORK_SUBMITTED,
        title="Work Submitted",
        message=f'An agent submitted work for "{bounty.title}".',
        reference_id=sub.id,
    )

    # Auto-approval: approve immediately and release escrow via the exchange
    if bounty.auto_approve:
        if bounty.escrow_id and bounty.escrow_id != "pending_claim":
            try:
                requester = await _get_requester(db, bounty.requester_id)
                exchange_svc.release_escrow(requester, bounty.escrow_id)
            except Exception as exc:
                logger.warning("Escrow release failed during auto-approve: %s", exc)

        await submission_service.approve_submission(db, sub, notes="Auto-approved")

        await create_notification(
            db,
            user_id=bounty.requester_id,
            type=NotificationType.PAYMENT_RELEASED,
            title="Payment Released (Auto-Approved)",
            message=f'Work on "{bounty.title}" was auto-approved and payment released.',
            reference_id=sub.id,
        )
        await create_notification(
            db,
            user_id=user.id,
            type=NotificationType.PAYMENT_RELEASED,
            title="Payment Received",
            message=f'Your work on "{bounty.title}" was approved. Payment released.',
            reference_id=sub.id,
        )

    await db.commit()
    await db.refresh(sub)
    return SubmissionResponse.model_validate(sub)


@router.get("/bounties/{bounty_id}/submissions", response_model=list[SubmissionResponse], tags=["submissions"])
async def list_bounty_submissions(
    bounty_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    bounty = await bounty_service.get_bounty(db, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")
    subs = await submission_service.list_submissions_for_bounty(db, bounty_id)
    return [SubmissionResponse.model_validate(s) for s in subs]


@router.get("/submissions/{submission_id}", response_model=SubmissionResponse)
async def get_submission(
    submission_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sub = await submission_service.get_submission(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    return SubmissionResponse.model_validate(sub)


@router.post("/submissions/{submission_id}/approve", response_model=SubmissionResponse)
async def approve_submission(
    submission_id: uuid.UUID,
    body: ScoredApprovalRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sub = await submission_service.get_submission(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    bounty = await bounty_service.get_bounty(db, sub.bounty_id)
    if not bounty or bounty.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Not your bounty")
    if sub.status != SubmissionStatus.PENDING_REVIEW:
        raise HTTPException(status_code=400, detail="Submission is not pending review")

    score = body.score if body else 100
    release_pct = body.release_percent if body else 100
    notes = body.notes if body else None

    if release_pct < 100 and bounty.escrow_id and bounty.escrow_id != "pending_claim":
        try:
            exchange_svc.partial_release(
                user,
                escrow_id=bounty.escrow_id,
                release_percent=release_pct,
                score=score,
                efficacy_check_at=body.efficacy_check_at.isoformat() if body and body.efficacy_check_at else None,
                efficacy_criteria=body.efficacy_criteria if body else None,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Failed to partially release escrow: {exc}")

        await submission_service.partially_approve_submission(
            db,
            sub,
            score=score,
            release_percent=release_pct,
            efficacy_check_at=body.efficacy_check_at if body else None,
            efficacy_criteria=body.efficacy_criteria if body else None,
            notes=notes,
        )

        await create_notification(
            db,
            user_id=sub.agent_user_id,
            type=NotificationType.PAYMENT_RELEASED,
            title="Partial Payment Released",
            message=f'Your work on "{bounty.title}" was partially approved ({release_pct}%). Holdback pending efficacy review.',
            reference_id=sub.id,
        )
    else:
        if bounty.escrow_id and bounty.escrow_id != "pending_claim":
            try:
                exchange_svc.release_escrow(user, bounty.escrow_id)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"Failed to release escrow: {exc}")

        await submission_service.approve_submission(db, sub, notes=notes)

        await create_notification(
            db,
            user_id=sub.agent_user_id,
            type=NotificationType.PAYMENT_RELEASED,
            title="Payment Released",
            message=f'Your work on "{bounty.title}" was approved. Payment released.',
            reference_id=sub.id,
        )

    await db.commit()
    await db.refresh(sub)
    return SubmissionResponse.model_validate(sub)


@router.post("/submissions/{submission_id}/reject", response_model=SubmissionResponse)
async def reject_submission(
    submission_id: uuid.UUID,
    body: ReviewRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sub = await submission_service.get_submission(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    bounty = await bounty_service.get_bounty(db, sub.bounty_id)
    if not bounty or bounty.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Not your bounty")
    if sub.status != SubmissionStatus.PENDING_REVIEW:
        raise HTTPException(status_code=400, detail="Submission is not pending review")

    notes = body.notes if body else None
    await submission_service.reject_submission(db, sub, notes=notes)

    await db.commit()
    await db.refresh(sub)
    return SubmissionResponse.model_validate(sub)


@router.post("/submissions/{submission_id}/dispute", response_model=SubmissionResponse)
async def dispute_submission(
    submission_id: uuid.UUID,
    body: DisputeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sub = await submission_service.get_submission(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    bounty = await bounty_service.get_bounty(db, sub.bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    is_requester = bounty.requester_id == user.id
    is_agent = sub.agent_user_id == user.id
    if not is_requester and not is_agent:
        raise HTTPException(status_code=403, detail="Not a party to this submission")

    # File dispute on exchange
    if bounty.escrow_id and bounty.escrow_id != "pending_claim":
        try:
            exchange_svc.dispute_escrow(user, bounty.escrow_id, reason=body.reason)
        except Exception as exc:
            logger.warning("SDK dispute call failed (non-fatal): %s", exc)

    await submission_service.dispute_submission(db, sub)

    notify_user_id = bounty.requester_id if is_agent else sub.agent_user_id
    await create_notification(
        db,
        user_id=notify_user_id,
        type=NotificationType.DISPUTE_FILED,
        title="Dispute Filed",
        message=f'A dispute was filed on "{bounty.title}": {body.reason}',
        reference_id=sub.id,
    )

    await db.commit()
    await db.refresh(sub)
    return SubmissionResponse.model_validate(sub)


@router.post("/submissions/{submission_id}/efficacy-review", response_model=SubmissionResponse)
async def efficacy_review(
    submission_id: uuid.UUID,
    body: EfficacyReviewRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sub = await submission_service.get_submission(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    bounty = await bounty_service.get_bounty(db, sub.bounty_id)
    if not bounty or bounty.requester_id != user.id:
        raise HTTPException(status_code=403, detail="Not your bounty")
    if sub.status != SubmissionStatus.PARTIALLY_APPROVED:
        raise HTTPException(
            status_code=400,
            detail="Submission is not awaiting efficacy review",
        )

    if bounty.escrow_id and bounty.escrow_id != "pending_claim":
        try:
            if body.action == "release":
                exchange_svc.release_escrow(user, bounty.escrow_id)
            else:
                exchange_svc.refund_escrow(
                    user, bounty.escrow_id, reason=body.notes or "Efficacy not met"
                )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to {body.action} holdback: {exc}",
            )

    await submission_service.complete_efficacy_review(
        db, sub, efficacy_score=body.score, notes=body.notes
    )

    action_label = "released" if body.action == "release" else "refunded"
    await create_notification(
        db,
        user_id=sub.agent_user_id,
        type=NotificationType.PAYMENT_RELEASED,
        title=f"Holdback {action_label.title()}",
        message=f'Efficacy review for "{bounty.title}": holdback {action_label} (score: {body.score}).',
        reference_id=sub.id,
    )

    await db.commit()
    await db.refresh(sub)
    return SubmissionResponse.model_validate(sub)
