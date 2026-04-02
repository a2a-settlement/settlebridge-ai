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
    mediator as mediator_svc,
    review_service,
    submission_service,
    training_service,
)
from app.models.score_history import BountyMode
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


async def _get_agent_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    from sqlalchemy import select

    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


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

    # AI-assisted review via Haiku
    ai_review: dict = {}
    try:
        ac = bounty.acceptance_criteria or {}
        ai_review = await review_service.review_deliverable(
            bounty_title=bounty.title,
            bounty_description=bounty.description,
            acceptance_criteria=ac if ac else None,
            reward_amount=bounty.reward_amount,
            difficulty=bounty.difficulty.value if bounty.difficulty else "medium",
            deliverable_content=body.deliverable.content,
            provenance=provenance_dict,
        )
        if ai_review:
            sub.ai_review = ai_review
            await db.flush()
    except Exception as exc:
        logger.warning("AI review failed (non-fatal): %s", exc)

    await create_notification(
        db,
        user_id=bounty.requester_id,
        type=NotificationType.WORK_SUBMITTED,
        title="Work Submitted",
        message=f'An agent submitted work for "{bounty.title}".',
        reference_id=sub.id,
    )

    # Score recording — write to score_history for all bounty modes.
    # Training bounties: trigger the Settlement Mediator for a structured verdict.
    # Production bounties: record the ai_review score as a lighter-weight ledger entry.
    try:
        agent_id = user.exchange_bot_id or str(user.id)
        bounty_mode = getattr(bounty, "mode", None) or BountyMode.PRODUCTION

        if bounty_mode == BountyMode.TRAINING and bounty.escrow_id and bounty.escrow_id != "pending_claim":
            # Trigger the Settlement Mediator for a structured training verdict
            task_type = (bounty.tags[0] if bounty.tags else None)
            verdict_dict = await mediator_svc.trigger_mediation(
                bounty.escrow_id,
                mode="training",
                task_type=task_type,
            )
            mediator_verdict = verdict_dict.get("verdict", {})
            numeric_score = float(mediator_verdict.get("confidence", 0.0))
            reasoning = mediator_verdict.get("reasoning")
            diagnostics = mediator_verdict.get("structured_diagnostic")

            # Determine the training_run_id by looking up the most recent running
            # run for this agent+bounty (harness always has exactly one active run)
            from sqlalchemy import select as sa_select
            from app.models.training_run import TrainingRun, TrainingRunStatus
            run_result = await db.execute(
                sa_select(TrainingRun)
                .where(
                    TrainingRun.agent_id == agent_id,
                    TrainingRun.target_bounty_id == bounty.id,
                    TrainingRun.status == TrainingRunStatus.RUNNING,
                )
                .order_by(TrainingRun.started_at.desc())
                .limit(1)
            )
            active_run = run_result.scalar_one_or_none()

            await training_service.record_score(
                db,
                agent_id=agent_id,
                bounty_id=bounty.id,
                task_type=task_type,
                numeric_score=numeric_score,
                reasoning=reasoning,
                diagnostics=diagnostics,
                mode=BountyMode.TRAINING,
                training_run_id=active_run.id if active_run else None,
                provenance=provenance_dict,
                iteration_stake=bounty.reward_amount,
            )
        elif ai_review:
            # Production bounty: record the ai_review score as a ledger entry
            ai_score_raw = ai_review.get("score", 100)
            numeric_score = float(ai_score_raw) / 100.0  # normalize 0–100 → 0.0–1.0
            await training_service.record_score(
                db,
                agent_id=agent_id,
                bounty_id=bounty.id,
                task_type=bounty.tags[0] if bounty.tags else None,
                numeric_score=numeric_score,
                reasoning=ai_review.get("notes"),
                diagnostics=None,
                mode=BountyMode.PRODUCTION,
                training_run_id=None,
                provenance=provenance_dict,
                iteration_stake=0,
            )
    except Exception as score_exc:
        logger.warning("Score recording failed (non-fatal): %s", score_exc)

    # Auto-approval: use AI review to decide score, holdback, or rejection
    if bounty.auto_approve:
        rec = ai_review.get("recommendation", "approve")
        ai_score = ai_review.get("score", 100)
        ai_holdback = ai_review.get("holdback", False)
        ai_holdback_pct = ai_review.get("holdback_percent", 20)
        ai_notes = ai_review.get("notes", "")
        has_escrow = bounty.escrow_id and bounty.escrow_id != "pending_claim"

        if rec == "reject":
            # AI says reject — refund escrow and reject submission
            if has_escrow:
                try:
                    requester = await _get_requester(db, bounty.requester_id)
                    exchange_svc.refund_escrow(
                        requester, bounty.escrow_id, reason=ai_notes or "AI review: rejected"
                    )
                except Exception as exc:
                    logger.warning("Escrow refund on AI rejection failed: %s", exc)
                bounty.escrow_id = None

            await submission_service.reject_submission(
                db, sub, notes=f"[AI auto-review] {ai_notes}"
            )
            await create_notification(
                db,
                user_id=user.id,
                type=NotificationType.SUBMISSION_REJECTED,
                title="Submission Rejected (Auto-Review)",
                message=f'Your submission for "{bounty.title}" did not pass automated review. {ai_notes}',
                reference_id=sub.id,
            )

        elif rec == "partial_approve" and ai_holdback and has_escrow:
            # AI says partial — release partial, hold back remainder
            release_pct = max(1, min(99, 100 - ai_holdback_pct))
            from datetime import datetime, timedelta, timezone
            check_at = datetime.now(timezone.utc) + timedelta(days=3)
            try:
                requester = await _get_requester(db, bounty.requester_id)
                exchange_svc.partial_release(
                    requester,
                    escrow_id=bounty.escrow_id,
                    release_percent=release_pct,
                    score=ai_score,
                    efficacy_check_at=check_at.isoformat(),
                    efficacy_criteria=ai_review.get("efficacy_criteria"),
                )
            except Exception as exc:
                logger.warning("Partial release failed during auto-approve: %s", exc)

            await submission_service.partially_approve_submission(
                db,
                sub,
                score=ai_score,
                release_percent=release_pct,
                efficacy_check_at=check_at,
                efficacy_criteria=ai_review.get("efficacy_criteria"),
                notes=f"[AI auto-review] {ai_notes}",
            )
            await create_notification(
                db,
                user_id=user.id,
                type=NotificationType.PAYMENT_RELEASED,
                title="Partial Payment Released (Auto-Review)",
                message=f'Your work on "{bounty.title}" was partially approved ({release_pct}%). Holdback pending efficacy review.',
                reference_id=sub.id,
            )

        else:
            # AI says approve (or no AI review available) — full release
            if has_escrow:
                try:
                    requester = await _get_requester(db, bounty.requester_id)
                    exchange_svc.release_escrow(requester, bounty.escrow_id)
                except Exception as exc:
                    logger.warning("Escrow release failed during auto-approve: %s", exc)

            notes = f"[AI auto-review, score: {ai_score}] {ai_notes}" if ai_review else "Auto-approved"
            await submission_service.approve_submission(db, sub, notes=notes)

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

    has_escrow = bounty.escrow_id and bounty.escrow_id != "pending_claim"

    if release_pct < 100 and has_escrow:
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
            if "400" in str(exc):
                logger.warning("Exchange partial_release returned 400 (likely already processed): %s", exc)
            else:
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
        if has_escrow:
            try:
                exchange_svc.release_escrow(user, bounty.escrow_id)
            except Exception as exc:
                if exchange_svc.is_escrow_expired(user, bounty.escrow_id):
                    logger.info("Escrow %s expired, recreating for release", bounty.escrow_id)
                    claim = await claim_service.get_claim(db, sub.claim_id)
                    if not claim:
                        raise HTTPException(status_code=500, detail="Associated claim not found")
                    agent_user = await _get_agent_user(db, sub.agent_user_id)
                    if not agent_user or not agent_user.exchange_api_key:
                        raise HTTPException(status_code=500, detail="Agent has no exchange credentials")
                    deliverable = sub.deliverable or {}
                    content = deliverable.get("content", "")
                    content_hash = compute_content_hash(content) if content else None
                    try:
                        new_id = exchange_svc.recreate_and_release(
                            requester=user,
                            provider_bot_id=claim.agent_exchange_bot_id,
                            provider_api_key=agent_user.exchange_api_key,
                            amount=bounty.reward_amount,
                            task_id=str(bounty.id),
                            content=content,
                            content_hash=content_hash,
                            provenance=sub.provenance,
                            required_attestation_level=bounty.provenance_tier.value if bounty.provenance_tier else None,
                        )
                        bounty.escrow_id = new_id
                        logger.info("Replaced expired escrow with %s", new_id)
                    except Exception as inner_exc:
                        raise HTTPException(
                            status_code=502,
                            detail=f"Failed to recreate and release escrow: {inner_exc}",
                        )
                else:
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

    has_escrow = bounty.escrow_id and bounty.escrow_id != "pending_claim"
    if has_escrow:
        try:
            exchange_svc.refund_escrow(
                user, bounty.escrow_id, reason=notes or "Submission rejected"
            )
            logger.info("Refunded escrow %s on rejection", bounty.escrow_id)
        except Exception as exc:
            logger.warning("Escrow refund on rejection failed (non-fatal): %s", exc)
        bounty.escrow_id = None

    await submission_service.reject_submission(db, sub, notes=notes)

    await create_notification(
        db,
        user_id=sub.agent_user_id,
        type=NotificationType.SUBMISSION_REJECTED,
        title="Submission Rejected",
        message=f'Your submission for "{bounty.title}" was rejected.{f" Notes: {notes}" if notes else ""}',
        reference_id=sub.id,
    )

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
