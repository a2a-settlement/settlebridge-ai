from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.notification import NotificationType
from app.models.submission import SubmissionStatus
from app.models.user import User
from app.schemas.submission import (
    DisputeRequest,
    EfficacyReviewRequest,
    PublicSubmissionResponse,
    ReviewRequest,
    ScoredApprovalRequest,
    SubmissionResponse,
    SubmitWorkRequest,
)
from app.middleware.auth import get_optional_user
from app.services import (
    bounty_service,
    claim_service,
    exchange as exchange_svc,
    review_service,
    submission_service,
    training_service,
)
from app.services.mediator import trigger_training_mediation
from app.services.notification_service import create_notification
from app.utils.helpers import compute_content_hash

import uuid as _uuid_mod
from sqlalchemy import select

router = APIRouter()
public_router = APIRouter()
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

    # Call SDK deliver if escrow exists (skip virtual training escrows)
    if (bounty.escrow_id and bounty.escrow_id != "pending_claim"
            and not bounty.escrow_id.startswith("training:")):
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

    # Fetch prior reviewed submissions for this bounty+claim to give the
    # reviewer iteration context (scores, issues flagged, improvement credit).
    prior_subs_for_review: list[dict] = []
    try:
        prior_subs = await submission_service.list_submissions_for_bounty(db, bounty.id)
        for ps in reversed(prior_subs):  # chronological order
            if ps.id == sub.id:
                continue
            if ps.claim_id != claim.id:
                continue
            if ps.ai_review or ps.score is not None:
                prior_subs_for_review.append({
                    "submitted_at": ps.submitted_at.isoformat() if ps.submitted_at else "",
                    "status": ps.status.value,
                    "score": ps.score,
                    "ai_review": ps.ai_review,
                })
    except Exception as exc:
        logger.warning("Failed to load prior submissions for review context (non-fatal): %s", exc)

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
            prior_submissions=prior_subs_for_review or None,
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

    # Training path: call mediator, record score, reset bounty for next iteration
    if claim.training_run_id:
        run = await training_service.get_run(db, claim.training_run_id)
        escrow_id = claim.virtual_escrow_id or bounty.escrow_id or ""
        is_virtual_escrow = escrow_id.startswith("training:")
        if run is not None and escrow_id and escrow_id != "pending_claim":
            mediator_result: dict | None = None

            # For virtual training escrows the real mediator cannot look up the
            # deliverable from the exchange (it doesn't exist there).  Try the
            # external mediator first; if it fails, fall back to the AI review
            # that was already performed in this request so scores are never lost.
            if not is_virtual_escrow:
                try:
                    mediator_result = await trigger_training_mediation(escrow_id, run.task_type)
                except Exception as exc:
                    logger.warning("Training mediation failed for real escrow (non-fatal): %s", exc)
            else:
                try:
                    mediator_result = await trigger_training_mediation(escrow_id, run.task_type)
                except Exception as exc:
                    logger.warning(
                        "Mediator rejected virtual escrow %s — using AI review score as fallback: %s",
                        escrow_id, exc,
                    )
                    # Build a synthetic mediator result from the AI review.
                    if ai_review:
                        raw_score = ai_review.get("score", 0)
                        issues = ai_review.get("issues", [])
                        mediator_result = {
                            "confidence": round(raw_score / 100.0, 4),
                            "reasoning": ai_review.get("notes", ""),
                            "structured_diagnostic": {
                                "actionable_gaps": issues,
                                "details": {"source": "ai_review", "raw_score": raw_score},
                            },
                            "verdict": {},
                            "_raw": {"source": "ai_review_fallback"},
                        }

            if mediator_result is not None:
                try:
                    score = float(mediator_result.get("confidence", 0.0))
                    await training_service.record_score(
                        db,
                        run=run,
                        submission=sub,
                        mediator_result=mediator_result,
                        iter_stake=100,
                    )
                    if not is_virtual_escrow:
                        try:
                            if score >= run.score_threshold:
                                exchange_svc.release_escrow(user, escrow_id)
                            else:
                                exchange_svc.refund_escrow(user, escrow_id, reason="training score below threshold")
                        except Exception as exc:
                            logger.warning("Training escrow settle failed (non-fatal): %s", exc)
                except Exception as exc:
                    logger.warning("Training score recording failed (non-fatal): %s", exc)
            else:
                logger.warning("No mediator result and no AI review fallback — skipping score for %s", escrow_id)

        # Restore bounty to OPEN so the harness can claim again next iteration
        from app.models.bounty import BountyStatus as _BS
        if bounty.status != _BS.OPEN:
            bounty.status = _BS.OPEN
        bounty.escrow_id = None
        await db.commit()
        await db.refresh(sub)
        return SubmissionResponse.model_validate(sub)

    await db.commit()
    await db.refresh(sub)
    return SubmissionResponse.model_validate(sub)


@router.get("/bounties/{bounty_id}/submissions", response_model=list[SubmissionResponse], tags=["submissions"])
async def list_bounty_submissions(
    bounty_id: uuid.UUID,
    user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    bounty = await bounty_service.get_bounty(db, bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    # Completed bounties are publicly readable so anonymous visitors can see
    # results on the Completed Results feed.  All other statuses require the
    # caller to be authenticated (owner, agent, or any logged-in user).
    from app.models.bounty import BountyStatus
    if bounty.status != BountyStatus.COMPLETED and user is None:
        raise HTTPException(status_code=401, detail="Authentication required")

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


# ── Public share endpoints ────────────────────────────────────────────────────

@router.post("/submissions/{submission_id}/share", response_model=SubmissionResponse, tags=["submissions"])
async def enable_share(
    submission_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enable public sharing for a submission. Returns a share_token usable at GET /share/{token}."""
    sub = await submission_service.get_submission(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    bounty = await bounty_service.get_bounty(db, sub.bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    is_agent = sub.agent_user_id == user.id
    is_requester = bounty.requester_id == user.id
    if not is_agent and not is_requester:
        raise HTTPException(status_code=403, detail="Not a party to this submission")

    if not sub.share_token:
        sub.share_token = _uuid_mod.uuid4()
    sub.public_share = True
    await db.commit()
    await db.refresh(sub)
    return SubmissionResponse.model_validate(sub)


@router.delete("/submissions/{submission_id}/share", response_model=SubmissionResponse, tags=["submissions"])
async def disable_share(
    submission_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Disable public sharing. The share_token is preserved but the link stops working."""
    sub = await submission_service.get_submission(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")

    bounty = await bounty_service.get_bounty(db, sub.bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    is_agent = sub.agent_user_id == user.id
    is_requester = bounty.requester_id == user.id
    if not is_agent and not is_requester:
        raise HTTPException(status_code=403, detail="Not a party to this submission")

    sub.public_share = False
    await db.commit()
    await db.refresh(sub)
    return SubmissionResponse.model_validate(sub)


def _build_share_html(data: PublicSubmissionResponse, share_token: str) -> str:
    """Render a styled HTML page for a shared submission."""
    import json as _json
    from html import escape

    status_meta = {
        SubmissionStatus.APPROVED:           ("✓ Verified",       "#2ea44f", "verified"),
        SubmissionStatus.PARTIALLY_APPROVED: ("✓ Partial",        "#f0883e", "partial"),
        SubmissionStatus.PENDING_REVIEW:     ("⏳ Pending Review", "#8b949e", "pending"),
        SubmissionStatus.REJECTED:           ("✗ Rejected",       "#da3633", "rejected"),
        SubmissionStatus.DISPUTED:           ("⚠ Disputed",       "#d29922", "disputed"),
    }
    status_label, status_color, _ = status_meta.get(data.status, ("Unknown", "#8b949e", "unknown"))
    score_html = f'<span class="score">{data.score}/100</span>' if data.score is not None else ""
    escrow_html = (
        f'<div class="meta-item"><span class="meta-label">Escrow</span>'
        f'<span class="meta-value mono">{escape(data.escrow_id[:16])}…</span></div>'
        if data.escrow_id else ""
    )

    # Extract image URLs from deliverable (markdown syntax and bare URLs)
    import re as _re
    raw_content = data.deliverable_content
    _img_bare = _re.compile(
        r'https?://\S+\.(?:png|jpg|jpeg|gif|webp)(?:\?[^\s\)\]"\'<>]*)?',
        _re.IGNORECASE,
    )
    _img_md = _re.compile(
        r'!\[[^\]]*\]\((https?://[^\)]+\.(?:png|jpg|jpeg|gif|webp)[^\)]*)\)',
        _re.IGNORECASE,
    )
    image_urls: list[str] = []
    seen: set[str] = set()
    for m in _img_md.finditer(raw_content):
        u = m.group(1)
        if u not in seen:
            image_urls.append(u)
            seen.add(u)
    for m in _img_bare.finditer(raw_content):
        u = m.group(0).rstrip(".,;)")
        if u not in seen:
            image_urls.append(u)
            seen.add(u)

    images_html = ""
    if image_urls:
        imgs = "".join(
            f'<figure class="chart-figure">'
            f'<img src="{escape(u)}" alt="Forecast chart" class="chart-img"'
            f' onerror="this.closest(\'figure\').style.display=\'none\'"/>'
            f'</figure>'
            for u in image_urls
        )
        images_html = f'<div class="chart-section">{imgs}</div>'

    # Pretty-print deliverable if it's JSON, otherwise render markdown-ish text
    try:
        parsed = _json.loads(raw_content)
        content_html = f'<pre class="deliverable json">{escape(_json.dumps(parsed, indent=2))}</pre>'
    except Exception:
        content_html = f'<pre class="deliverable">{escape(raw_content)}</pre>'

    ai_notes_html = ""
    if data.ai_review:
        ai_score = data.ai_review.get("score", "")
        ai_notes = data.ai_review.get("notes", "")
        ai_issues = data.ai_review.get("issues", [])
        issues_html = "".join(f"<li>{escape(i)}</li>" for i in ai_issues)
        ai_notes_html = f"""
        <div class="section">
          <h2>AI Review <span class="ai-score">{ai_score}/100</span></h2>
          <p class="ai-notes">{escape(ai_notes)}</p>
          {"<ul class='issues'>" + issues_html + "</ul>" if issues_html else ""}
        </div>"""

    badge_url = f"https://settlebridge.ai/shared/{share_token}/badge.svg"
    page_url = f"https://settlebridge.ai/shared/{share_token}"
    submitted = data.submitted_at.strftime("%B %d, %Y") if data.submitted_at else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>{escape(data.bounty_title)} — SettleBridge</title>
  <link rel="icon" type="image/svg+xml" href="https://market.settlebridge.ai/settlebridge-favicon.svg"/>
  <link rel="shortcut icon" href="https://market.settlebridge.ai/favicon.ico"/>
  <meta property="og:title" content="{escape(data.bounty_title)}"/>
  <meta property="og:description" content="Agent: {escape(data.agent_display_name)} · Status: {status_label}{f' · Score: {data.score}/100' if data.score else ''} · Verified on SettleBridge"/>
  <meta property="og:url" content="{page_url}"/>
  <meta property="og:site_name" content="SettleBridge"/>
  <meta property="og:type" content="article"/>
  <meta name="twitter:card" content="summary"/>
  <meta name="twitter:title" content="{escape(data.bounty_title)}"/>
  <meta name="twitter:description" content="Agent: {escape(data.agent_display_name)} · {status_label}{f' · {data.score}/100' if data.score else ''} · Settled on SettleBridge"/>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin=""/>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet"/>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0d1117; color: #e6edf3; font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; min-height: 100vh; }}
    a {{ color: #22c55e; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .topbar {{ background: #0f172a; border-bottom: 1px solid #1e293b; padding: 0 24px; height: 64px; display: flex; align-items: center; justify-content: space-between; }}
    .brand {{ display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 18px; color: #fff; letter-spacing: -.3px; text-decoration: none; }}
    .brand:hover {{ text-decoration: none; }}
    .brand svg {{ flex-shrink: 0; }}
    .market-link {{ font-size: 13px; color: #94a3b8; transition: color .15s; }}
    .market-link:hover {{ color: #fff; text-decoration: none; }}
    .container {{ max-width: 860px; margin: 0 auto; padding: 40px 24px 80px; }}
    .header {{ margin-bottom: 32px; }}
    .status-badge {{ display: inline-flex; align-items: center; gap: 6px; background: {status_color}22; color: {status_color}; border: 1px solid {status_color}55; border-radius: 20px; padding: 4px 12px; font-size: 13px; font-weight: 600; margin-bottom: 14px; }}
    h1 {{ font-size: 24px; font-weight: 700; line-height: 1.3; color: #f1f5f9; margin-bottom: 10px; }}
    .score {{ background: #1f2937; border: 1px solid #374151; border-radius: 6px; padding: 2px 8px; font-size: 13px; font-weight: 600; color: #f0883e; margin-left: 8px; }}
    .meta-row {{ display: flex; flex-wrap: wrap; gap: 20px; margin-top: 14px; }}
    .meta-item {{ display: flex; flex-direction: column; gap: 2px; }}
    .meta-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: #64748b; }}
    .meta-value {{ font-size: 13px; color: #cbd5e1; }}
    .mono {{ font-family: "SFMono-Regular", Consolas, monospace; font-size: 12px; }}
    .section {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 24px; margin-bottom: 20px; }}
    .section h2 {{ font-size: 15px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }}
    .ai-score {{ font-size: 14px; background: #052e16; color: #22c55e; border: 1px solid #166534; border-radius: 6px; padding: 2px 8px; font-weight: 700; }}
    .deliverable {{ background: #020617; border: 1px solid #1e293b; border-radius: 6px; padding: 16px; font-size: 12.5px; font-family: "SFMono-Regular", Consolas, monospace; color: #cbd5e1; overflow-x: auto; white-space: pre-wrap; word-break: break-word; max-height: 480px; overflow-y: auto; line-height: 1.6; }}
    .ai-notes {{ font-size: 14px; color: #cbd5e1; line-height: 1.65; margin-bottom: 14px; }}
    .issues {{ padding-left: 20px; }}
    .issues li {{ font-size: 13px; color: #64748b; line-height: 1.6; margin-bottom: 6px; }}
    .embed-box {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 20px 24px; }}
    .embed-box h3 {{ font-size: 13px; font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 12px; }}
    .embed-code {{ background: #020617; border: 1px solid #1e293b; border-radius: 6px; padding: 12px; font-family: monospace; font-size: 12px; color: #cbd5e1; overflow-x: auto; white-space: nowrap; }}
    .badge-preview {{ margin-top: 12px; }}
    .chart-section {{ margin-bottom: 20px; display: flex; flex-direction: column; gap: 16px; }}
    .chart-figure {{ margin: 0; }}
    .chart-img {{ max-width: 100%; width: 100%; border-radius: 8px; border: 1px solid #1e293b; display: block; background: #020617; }}
  </style>
</head>
<body>
  <div class="topbar">
    <a class="brand" href="https://market.settlebridge.ai">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" fill="#22c55e"/>
        <path d="m9 12 2 2 4-4" stroke="#0f172a" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      SettleBridge
    </a>
    <a class="market-link" href="https://market.settlebridge.ai">Open Marketplace →</a>
  </div>
  <div class="container">
    <div class="header">
      <div class="status-badge">{status_label}{score_html}</div>
      <h1>{escape(data.bounty_title)}</h1>
      <div class="meta-row">
        <div class="meta-item">
          <span class="meta-label">Agent</span>
          <span class="meta-value">{escape(data.agent_display_name)}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Submitted</span>
          <span class="meta-value">{submitted}</span>
        </div>
        {escrow_html}
      </div>
    </div>

    <div class="section">
      <h2>Deliverable</h2>
      {images_html}
      {content_html}
    </div>

    {ai_notes_html}

    <div class="embed-box">
      <h3>Embed this result</h3>
      <div class="embed-code">[![SettleBridge]({badge_url})]({page_url})</div>
      <div class="badge-preview"><img src="{badge_url}" alt="SettleBridge badge"/></div>
    </div>
  </div>
</body>
</html>"""


@public_router.get("/share/{share_token}", tags=["public"])
async def get_shared_submission(
    share_token: uuid.UUID,
    request: "Request",
    db: AsyncSession = Depends(get_db),
):
    """Public read-only view — returns HTML for browsers, JSON for API clients."""
    from app.models.submission import Submission
    from app.models.user import User as UserModel
    import json as _json

    result = await db.execute(
        select(Submission).where(
            Submission.share_token == share_token,
            Submission.public_share.is_(True),
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(status_code=404, detail="Shared submission not found")

    bounty = await bounty_service.get_bounty(db, sub.bounty_id)
    if not bounty:
        raise HTTPException(status_code=404, detail="Bounty not found")

    agent_result = await db.execute(select(UserModel).where(UserModel.id == sub.agent_user_id))
    agent = agent_result.scalar_one_or_none()
    agent_name = agent.display_name if agent else "Unknown Agent"

    deliverable = sub.deliverable or {}
    data = PublicSubmissionResponse(
        share_token=sub.share_token,
        bounty_title=bounty.title,
        bounty_description=bounty.description,
        agent_display_name=agent_name,
        deliverable_content=deliverable.get("content", ""),
        deliverable_content_type=deliverable.get("content_type", "text/plain"),
        provenance=sub.provenance,
        status=sub.status,
        submitted_at=sub.submitted_at,
        reviewed_at=sub.reviewed_at,
        score=sub.score,
        ai_review=sub.ai_review,
        escrow_id=bounty.escrow_id if bounty.escrow_id != "pending_claim" else None,
    )

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        html = _build_share_html(data, str(share_token))
        return HTMLResponse(content=html)

    return data


def _build_badge_svg(status: SubmissionStatus, score: int | None, reward: int | None) -> str:
    """Generate a shields.io-style SVG badge for a shared submission."""
    status_config = {
        SubmissionStatus.APPROVED: ("#2ea44f", "✓ Verified"),
        SubmissionStatus.PARTIALLY_APPROVED: ("#f0883e", "✓ Partial"),
        SubmissionStatus.PENDING_REVIEW: ("#6e7681", "⏳ Pending"),
        SubmissionStatus.REJECTED: ("#da3633", "✗ Rejected"),
        SubmissionStatus.DISPUTED: ("#d29922", "⚠ Disputed"),
    }
    right_color, right_label = status_config.get(status, ("#6e7681", "Unknown"))

    score_part = f" · {score}/100" if score is not None else ""
    reward_part = f" · {reward} ATE" if reward else ""
    right_text = f"{right_label}{score_part}{reward_part}"

    left_text = "SettleBridge"
    left_w = len(left_text) * 7 + 14
    right_w = len(right_text) * 7 + 14
    total_w = left_w + right_w

    return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{total_w}" height="20" role="img" aria-label="{left_text}: {right_text}">
  <title>{left_text}: {right_text}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="{total_w}" height="20" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="{left_w}" height="20" fill="#1a1a2e"/>
    <rect x="{left_w}" width="{right_w}" height="20" fill="{right_color}"/>
    <rect width="{total_w}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="110">
    <text aria-hidden="true" x="{left_w * 5}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(left_w - 10) * 10}" lengthAdjust="spacing">{left_text}</text>
    <text x="{left_w * 5}" y="140" transform="scale(.1)" fill="#fff" textLength="{(left_w - 10) * 10}" lengthAdjust="spacing">{left_text}</text>
    <text aria-hidden="true" x="{left_w * 10 + right_w * 5}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="{(right_w - 10) * 10}" lengthAdjust="spacing">{right_text}</text>
    <text x="{left_w * 10 + right_w * 5}" y="140" transform="scale(.1)" fill="#fff" textLength="{(right_w - 10) * 10}" lengthAdjust="spacing">{right_text}</text>
  </g>
</svg>"""


@public_router.get("/share/{share_token}/badge.svg", tags=["public"])
async def get_share_badge(
    share_token: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Returns an embeddable SVG badge for a shared submission."""
    from app.models.submission import Submission

    result = await db.execute(
        select(Submission).where(
            Submission.share_token == share_token,
            Submission.public_share.is_(True),
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        svg = _build_badge_svg(SubmissionStatus.REJECTED, None, None).replace("✗ Rejected", "Not Found")
        return Response(content=svg, media_type="image/svg+xml",
                        headers={"Cache-Control": "no-cache"})

    bounty = await bounty_service.get_bounty(db, sub.bounty_id)
    reward = bounty.reward_amount if bounty else None

    svg = _build_badge_svg(sub.status, sub.score, reward)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "max-age=300"},
    )


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
