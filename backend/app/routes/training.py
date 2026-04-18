from __future__ import annotations

import uuid
from datetime import datetime
from html import escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.claim import Claim
from app.models.submission import Submission
from app.models.training_run import TrainingRun, TrainingTranscript
from app.models.score_history import ScoreHistory
from app.models.user import User
from app.services import training_service

router = APIRouter()
training_public_router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CreateRunRequest(BaseModel):
    bounty_id: uuid.UUID
    max_iterations: int = 10
    stake_budget: int = 1000
    score_threshold: float = 0.85
    task_type: str


class TrainingRunResponse(BaseModel):
    run_id: uuid.UUID
    status: str
    bounty_id: uuid.UUID
    max_iterations: int
    stake_budget: int
    stake_spent: int
    score_threshold: float
    task_type: str
    iterations_completed: int
    created_at: datetime
    public: bool = False
    public_title: str | None = None

    model_config = {"from_attributes": True}


class ScoreHistoryRow(BaseModel):
    id: uuid.UUID
    agent_user_id: uuid.UUID
    bounty_id: uuid.UUID
    training_run_id: uuid.UUID | None = None
    task_type: str | None = None
    numeric_score: float
    reasoning: str | None = None
    diagnostics: dict | None = None
    mode: str
    provenance_hash: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class TranscriptResponse(BaseModel):
    id: uuid.UUID
    training_run_id: uuid.UUID
    agent_id: str
    bounty_id: uuid.UUID
    total_iterations: int
    total_stake_spent: int
    final_training_ema: float
    merkle_root: str
    signed_payload: dict[str, Any]
    generated_at: datetime

    model_config = {"from_attributes": True}


class PublishRequest(BaseModel):
    title: str | None = None


class PublicCardData(BaseModel):
    run_id: uuid.UUID
    public_title: str
    bounty_title: str
    agent_display_name: str
    status: str
    iterations: int
    scores: list[float]
    final_ema: float
    score_threshold: float
    threshold_reached: bool
    merkle_root: str | None
    created_at: datetime
    completed_at: datetime | None


# ---------------------------------------------------------------------------
# Authenticated endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/training/runs",
    response_model=TrainingRunResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["training"],
)
async def create_training_run(
    body: CreateRunRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        run = await training_service.create_run(
            db,
            agent_user_id=user.id,
            bounty_id=body.bounty_id,
            max_iterations=body.max_iterations,
            stake_budget=body.stake_budget,
            score_threshold=body.score_threshold,
            task_type=body.task_type,
        )
        await db.commit()
        await db.refresh(run)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return TrainingRunResponse(
        run_id=run.id,
        status=run.status.value,
        bounty_id=run.bounty_id,
        max_iterations=run.max_iterations,
        stake_budget=run.stake_budget,
        stake_spent=run.stake_spent,
        score_threshold=run.score_threshold,
        task_type=run.task_type,
        iterations_completed=run.iterations_completed,
        created_at=run.created_at,
        public=run.public,
        public_title=run.public_title,
    )


@router.post(
    "/training/runs/{run_id}/complete",
    response_model=TranscriptResponse,
    tags=["training"],
)
async def complete_training_run(
    run_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        transcript = await training_service.complete_run(
            db, run_id=run_id, agent_user_id=user.id
        )
        await db.commit()
        await db.refresh(transcript)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    return _transcript_response(transcript)


@router.post(
    "/training/runs/{run_id}/publish",
    tags=["training"],
)
async def publish_training_run(
    run_id: uuid.UUID,
    body: PublishRequest | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Opt a completed training run into public visibility."""
    run = await training_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Training run not found")
    if run.agent_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your training run")

    run.public = True
    snapshot_title = (run.bounty_snapshot or {}).get("title", "Training Run")
    run.public_title = (body.title if body and body.title else None) or snapshot_title
    await db.commit()

    return {
        "public": True,
        "public_title": run.public_title,
        "card_url": f"/api/training/runs/{run_id}/card",
        "card_html_url": f"/api/training/runs/{run_id}/card.html",
    }


@router.post(
    "/training/runs/{run_id}/unpublish",
    tags=["training"],
)
async def unpublish_training_run(
    run_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    run = await training_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Training run not found")
    if run.agent_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your training run")

    run.public = False
    await db.commit()
    return {"public": False}


@router.get(
    "/training/runs/{run_id}/transcript",
    response_model=TranscriptResponse,
    tags=["training"],
)
async def get_training_transcript(
    run_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    run = await training_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Training run not found")
    if run.agent_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your training run")

    transcript = (
        await db.execute(
            select(TrainingTranscript).where(TrainingTranscript.training_run_id == run_id)
        )
    ).scalar_one_or_none()

    if transcript is None:
        raise HTTPException(
            status_code=404,
            detail="Transcript not yet generated — POST /training/runs/{run_id}/complete first",
        )

    return _transcript_response(transcript)


@router.get(
    "/score-history",
    response_model=list[ScoreHistoryRow],
    tags=["training"],
)
async def list_score_history(
    training_run_id: uuid.UUID | None = Query(default=None),
    agent_id: str | None = Query(default=None, description="SettleBridge user UUID of the agent"),
    mode: str | None = Query(default=None, description="training or production"),
    task_type: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent_user_id: uuid.UUID | None = None
    if agent_id:
        try:
            agent_user_id = uuid.UUID(agent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="agent_id must be a valid UUID")
    elif training_run_id is None:
        agent_user_id = user.id

    rows = await training_service.get_score_history(
        db,
        training_run_id=training_run_id,
        agent_user_id=agent_user_id,
        mode=mode,
        task_type=task_type,
        limit=limit,
        offset=offset,
    )
    return [_score_row_response(r) for r in rows]


# ---------------------------------------------------------------------------
# Public (unauthenticated) endpoints
# ---------------------------------------------------------------------------

@training_public_router.get(
    "/training/public",
    tags=["training-public"],
)
async def list_public_training_runs(
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List all opt-in public training runs, newest first."""
    from app.models.user import User as UserModel
    from app.models.bounty import Bounty

    stmt = (
        select(TrainingRun)
        .where(TrainingRun.public.is_(True))
        .order_by(TrainingRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    runs = (await db.execute(stmt)).scalars().all()

    results = []
    for run in runs:
        scores, ema, merkle = await _load_run_scores(db, run)
        agent = await _load_agent(db, run.agent_user_id)
        bounty_title = (run.bounty_snapshot or {}).get("title", "")
        final_sub = await _load_final_submission(db, run)
        results.append(
            _public_card_dict(run, scores, ema, merkle, agent, bounty_title, final_sub)
        )
    return results


@training_public_router.get(
    "/training/runs/{run_id}/card",
    tags=["training-public"],
)
async def get_training_card(
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public JSON or HTML card for a training run (only if public=True)."""
    run = await training_service.get_run(db, run_id)
    if run is None or not run.public:
        raise HTTPException(status_code=404, detail="Training card not found or not public")

    scores, ema, merkle = await _load_run_scores(db, run)
    agent = await _load_agent(db, run.agent_user_id)
    bounty_title = (run.bounty_snapshot or {}).get("title", "")
    final_sub = await _load_final_submission(db, run)
    card = _public_card_dict(run, scores, ema, merkle, agent, bounty_title, final_sub)

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return HTMLResponse(_build_training_card_html(card))
    return JSONResponse(card)


@training_public_router.get(
    "/training/runs/{run_id}/card.html",
    response_class=HTMLResponse,
    tags=["training-public"],
)
async def get_training_card_html(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Standalone HTML page for a public training run card."""
    run = await training_service.get_run(db, run_id)
    if run is None or not run.public:
        raise HTTPException(status_code=404, detail="Training card not found or not public")

    scores, ema, merkle = await _load_run_scores(db, run)
    agent = await _load_agent(db, run.agent_user_id)
    bounty_title = (run.bounty_snapshot or {}).get("title", "")
    final_sub = await _load_final_submission(db, run)
    card = _public_card_dict(run, scores, ema, merkle, agent, bounty_title, final_sub)
    return HTMLResponse(_build_training_card_html(card))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_run_scores(db: AsyncSession, run: TrainingRun):
    """Return (scores, ema, merkle_root) for a run."""
    transcript = (
        await db.execute(
            select(TrainingTranscript).where(TrainingTranscript.training_run_id == run.id)
        )
    ).scalar_one_or_none()

    if transcript:
        payload = transcript.signed_payload or {}
        scores = [float(s) for s in payload.get("score_trajectory", [])]
        ema = float(transcript.final_training_ema)
        merkle = transcript.merkle_root
    else:
        # Run still in progress — load scores directly
        rows = await training_service.get_score_history(db, training_run_id=run.id, limit=500)
        scores = [r.numeric_score for r in rows]
        from app.services.training_service import compute_ema
        ema = compute_ema(scores)
        merkle = None

    return scores, ema, merkle


async def _load_agent(db: AsyncSession, agent_user_id: uuid.UUID) -> User | None:
    return (
        await db.execute(select(User).where(User.id == agent_user_id))
    ).scalar_one_or_none()


async def _load_final_submission(db: AsyncSession, run: TrainingRun) -> dict | None:
    """Return a summary dict for the highest-scoring submission in this training run.

    Picks the submission with the highest ai_review['score'] so the displayed
    result is consistent with the score_history values shown in the chart bars.
    Falls back to most-recently-submitted if no ai_review exists.
    """
    result = await db.execute(
        select(Submission)
        .join(Claim, Claim.id == Submission.claim_id)
        .where(Claim.training_run_id == run.id)
        .where(Submission.ai_review.isnot(None))
        .order_by(
            func.cast(Submission.ai_review["score"].as_string(), Integer).desc(),
            Submission.submitted_at.desc(),
        )
        .limit(1)
    )
    sub = result.scalar_one_or_none()

    if sub is None:
        # Fall back to the most recently submitted one (no ai_review yet)
        result = await db.execute(
            select(Submission)
            .join(Claim, Claim.id == Submission.claim_id)
            .where(Claim.training_run_id == run.id)
            .order_by(Submission.submitted_at.desc())
            .limit(1)
        )
        sub = result.scalar_one_or_none()

    if sub is None:
        return None

    ai_review = sub.ai_review or {}
    content = (sub.deliverable or {}).get("content", "") if sub.deliverable else ""
    return {
        "submission_id": str(sub.id),
        "content": content,
        "ai_score": ai_review.get("score"),
        "ai_recommendation": ai_review.get("recommendation"),
        "ai_notes": ai_review.get("notes", ""),
        "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
        "status": sub.status.value,
    }


def _public_card_dict(
    run: TrainingRun,
    scores: list[float],
    ema: float,
    merkle: str | None,
    agent: User | None,
    bounty_title: str,
    final_submission: dict | None = None,
) -> dict:
    agent_name = (
        (agent.exchange_bot_id or agent.email.split("@")[0]) if agent else "Agent"
    )
    threshold_reached = ema >= run.score_threshold if scores else False
    return {
        "run_id": str(run.id),
        "bounty_id": str(run.bounty_id),
        "public_title": run.public_title or bounty_title,
        "bounty_title": bounty_title,
        "agent_display_name": agent_name,
        "status": run.status.value,
        "iterations": run.iterations_completed,
        "scores": scores,
        "last_score": round(scores[-1], 4) if scores else None,
        "final_ema": round(ema, 4),
        "score_threshold": run.score_threshold,
        "threshold_reached": threshold_reached,
        "merkle_root": merkle,
        "created_at": run.created_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "final_submission": final_submission,
    }


def _build_score_svg(scores: list[float], threshold: float, ema: float) -> str:
    """Build an inline SVG bar chart with auto-scaled Y-axis, EMA overlay and threshold line."""
    if not scores:
        return '<svg width="100%" height="100" viewBox="0 0 400 100"><text x="200" y="50" text-anchor="middle" fill="#9ca3af" font-size="13">No iterations yet</text></svg>'

    w, h = 400, 100
    pad_left, pad_right, pad_top, pad_bottom = 36, 8, 10, 20
    chart_w = w - pad_left - pad_right
    chart_h = h - pad_top - pad_bottom

    # Auto-scale Y axis to the actual data range (include threshold)
    all_vals = scores + [threshold]
    raw_min = min(all_vals)
    raw_max = max(all_vals)
    data_range = raw_max - raw_min
    # Padding: at least 4 percentage points on each side, or 20% of range
    pad_val = max(0.04, data_range * 0.20)
    y_min = max(0.0, raw_min - pad_val)
    y_max = min(1.0, raw_max + pad_val)
    # Ensure minimum visible range of 10 points
    if y_max - y_min < 0.10:
        mid = (y_min + y_max) / 2
        y_min = max(0.0, mid - 0.05)
        y_max = min(1.0, mid + 0.05)
    y_range = y_max - y_min

    def to_y(v: float) -> float:
        """Map a score value to an SVG y-coordinate (higher value = lower y)."""
        return pad_top + chart_h * (1.0 - (v - y_min) / y_range)

    def to_bar_h(v: float) -> float:
        return chart_h * (v - y_min) / y_range

    n = len(scores)
    bar_gap = max(2, min(6, chart_w // (n * 4)))
    bar_w = max(6, (chart_w - bar_gap * (n - 1)) / n)

    # Y-axis grid lines and labels (3–4 ticks)
    grid_parts: list[str] = []
    tick_count = 4
    for ti in range(tick_count + 1):
        tick_val = y_min + (y_range * ti / tick_count)
        ty = to_y(tick_val)
        pct_label = f"{tick_val * 100:.0f}%"
        grid_parts.append(
            f'<line x1="{pad_left}" y1="{ty:.1f}" x2="{w - pad_right}" y2="{ty:.1f}" '
            f'stroke="#e5e7eb" stroke-width="0.8"/>'
            f'<text x="{pad_left - 3}" y="{ty + 3:.1f}" text-anchor="end" '
            f'font-size="8" fill="#9ca3af">{pct_label}</text>'
        )

    # Bars
    bars: list[str] = []
    for i, s in enumerate(scores):
        x = pad_left + i * (bar_w + bar_gap)
        bh = to_bar_h(s)
        by = to_y(s)
        color = "#22c55e" if s >= threshold else ("#f59e0b" if s >= threshold * 0.85 else "#ef4444")
        bars.append(
            f'<rect x="{x:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" '
            f'fill="{color}" rx="2" opacity="0.85"/>'
        )
        # Score value label above bar
        label_y = max(pad_top + 9, by - 3)
        bars.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{label_y:.1f}" text-anchor="middle" '
            f'font-size="8.5" font-weight="600" fill="{color}">{s * 100:.0f}%</text>'
        )
        # Iteration number below chart
        bars.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{h - 4}" text-anchor="middle" '
            f'font-size="8" fill="#6b7280">#{i + 1}</text>'
        )

    # EMA line
    ema_pts: list[str] = []
    running_ema = 0.0
    alpha = 2 / (min(n, 10) + 1)
    for i, s in enumerate(scores):
        running_ema = alpha * s + (1 - alpha) * running_ema if i > 0 else s
        cx = pad_left + i * (bar_w + bar_gap) + bar_w / 2
        ema_pts.append(f"{cx:.1f},{to_y(running_ema):.1f}")

    ema_line = (
        f'<polyline points="{" ".join(ema_pts)}" fill="none" stroke="#6366f1" '
        f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        if len(ema_pts) > 1 else ""
    )

    # Threshold line
    thresh_y = to_y(threshold)
    thresh_label = f"{threshold * 100:.0f}% threshold"
    thresh_line = (
        f'<line x1="{pad_left}" y1="{thresh_y:.1f}" x2="{w - pad_right}" y2="{thresh_y:.1f}" '
        f'stroke="#f97316" stroke-width="1.5" stroke-dasharray="5 3"/>'
        f'<text x="{w - pad_right - 2}" y="{thresh_y - 3:.1f}" text-anchor="end" '
        f'font-size="8.5" fill="#f97316" font-weight="600">{thresh_label}</text>'
    )

    return (
        f'<svg width="100%" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">'
        + "".join(grid_parts)
        + "".join(bars)
        + thresh_line
        + ema_line
        + "</svg>"
    )


def _build_training_card_html(card: dict) -> str:
    title = escape(card["public_title"])
    agent = escape(card["agent_display_name"])
    bounty = escape(card["bounty_title"])
    run_status = card["status"]
    iterations = card["iterations"]
    scores = card["scores"]
    ema = card["final_ema"]
    last_score = card.get("last_score")
    threshold = card["score_threshold"]
    threshold_reached = card["threshold_reached"]
    merkle = card.get("merkle_root") or ""
    completed_at = card.get("completed_at") or ""
    run_id = card["run_id"]
    bounty_id = card.get("bounty_id") or ""
    final_sub = card.get("final_submission") or {}

    status_label = "Completed" if run_status == "COMPLETED" else "In Progress"
    status_color = "#22c55e" if run_status == "COMPLETED" else "#6366f1"
    last_score_pct = f"{last_score * 100:.1f}%" if last_score is not None else "—"
    last_score_color = "#22c55e" if (last_score or 0) >= threshold else "#f59e0b"
    ema_pct = f"{ema * 100:.1f}%"
    thresh_pct = f"{threshold * 100:.0f}%"

    chart_svg = _build_score_svg(scores, threshold, ema)
    merkle_short = merkle[:16] + "…" if len(merkle) > 16 else merkle
    date_str = completed_at[:10] if completed_at else ""

    # Final deliverable section
    fs_html = ""
    if final_sub:
        ai_score = final_sub.get("ai_score")
        ai_notes = escape(final_sub.get("ai_notes", "") or "")
        content = final_sub.get("content", "") or ""
        rec = final_sub.get("ai_recommendation", "") or ""
        sub_status = final_sub.get("status", "") or ""

        score_color = "#22c55e" if (ai_score or 0) >= int(threshold * 100) else "#f59e0b"
        score_badge = f'<span style="font-weight:700;color:{score_color}">{ai_score}/100</span>' if ai_score is not None else ""
        rec_label = rec.replace("_", " ").title() if rec else ""
        status_label_sub = sub_status.replace("_", " ").title() if sub_status else ""

        # Truncate content preview to ~600 chars
        preview = content[:600].strip()
        if len(content) > 600:
            preview += "…"
        preview_escaped = escape(preview)

        bounty_link = f'https://market.settlebridge.ai/bounties/{bounty_id}' if bounty_id else "#"

        fs_html = f"""
    <div class="final-result">
      <div class="final-header">
        <div class="final-label">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="color:#6366f1"><path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
          Final Iteration Result
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          {score_badge}
          {f'<span class="rec-chip">{rec_label}</span>' if rec_label else ''}
        </div>
      </div>
      {f'<div class="ai-notes">{ai_notes}</div>' if ai_notes else ''}
      {f'<pre class="content-preview">{preview_escaped}</pre>' if preview_escaped else ''}
      <a href="{bounty_link}" class="view-link" target="_blank">View full deliverable on SettleBridge →</a>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>{title} · SettleBridge Training</title>
  <meta property="og:title" content="{title}"/>
  <meta property="og:description" content="Agent: {agent} · EMA: {ema_pct} · Threshold: {thresh_pct} · {iterations} iterations · SettleBridge Self-Improving Agent"/>
  <meta name="twitter:card" content="summary"/>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}
    .card{{background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,.10);width:100%;max-width:540px;overflow:hidden}}
    .header{{background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);padding:24px;color:#fff}}
    .badge{{display:inline-flex;align-items:center;gap:6px;background:rgba(99,102,241,.25);border:1px solid rgba(99,102,241,.5);color:#a5b4fc;font-size:11px;font-weight:600;padding:3px 10px;border-radius:99px;letter-spacing:.5px;text-transform:uppercase;margin-bottom:10px}}
    .badge svg{{width:12px;height:12px}}
    .title{{font-size:18px;font-weight:700;line-height:1.3;margin-bottom:4px}}
    .agent{{font-size:13px;color:#94a3b8}}
    .body{{padding:20px}}
    .chart-label{{font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px}}
    .chart-wrap{{background:#f8fafc;border-radius:8px;padding:10px 8px 4px;margin-bottom:16px;min-height:110px}}
    .stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:8px}}
    .stat{{background:#f8fafc;border-radius:8px;padding:10px 12px}}
    .stat-val{{font-size:18px;font-weight:700;color:#0f172a}}
    .stat-lbl{{font-size:11px;color:#6b7280;margin-top:2px}}
    .ema-note{{font-size:11px;color:#9ca3af;text-align:right;margin-bottom:12px;padding-right:2px}}
    .footer{{border-top:1px solid #f1f5f9;padding:14px 20px;display:flex;align-items:center;justify-content:space-between;background:#fafafa}}
    .verified{{display:flex;align-items:center;gap:6px;font-size:11px;color:#64748b}}
    .merkle{{font-family:monospace;font-size:10px;color:#94a3b8}}
    .status-chip{{font-size:11px;font-weight:600;padding:3px 10px;border-radius:99px;color:#fff}}
    .final-result{{border-top:1px solid #f1f5f9;padding:16px 20px;background:#fafafa}}
    .final-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}}
    .final-label{{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:#4b5563;text-transform:uppercase;letter-spacing:.4px}}
    .rec-chip{{font-size:10px;font-weight:600;background:#e0e7ff;color:#4338ca;padding:2px 8px;border-radius:99px}}
    .ai-notes{{font-size:12px;color:#6b7280;line-height:1.5;margin-bottom:10px;font-style:italic}}
    .content-preview{{font-size:11px;color:#374151;line-height:1.6;background:#fff;border:1px solid #e5e7eb;border-radius:6px;padding:10px;white-space:pre-wrap;word-break:break-word;max-height:220px;overflow:hidden;margin-bottom:10px;font-family:inherit}}
    .view-link{{font-size:12px;font-weight:600;color:#6366f1;text-decoration:none}}
    .view-link:hover{{text-decoration:underline}}
    a{{color:#6366f1;text-decoration:none}}
    a:hover{{text-decoration:underline}}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div class="badge">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M13 7l5 5m0 0l-5 5m5-5H6"/></svg>
        Self-Improving Agent
      </div>
      <div class="title">{title}</div>
      <div class="agent">{agent} &middot; {bounty}</div>
    </div>
    <div class="body">
      <div class="chart-label">Score Progression</div>
      <div class="chart-wrap">{chart_svg}</div>
      <div class="stats">
        <div class="stat">
          <div class="stat-val" style="color:{last_score_color}">{last_score_pct}</div>
          <div class="stat-lbl">Last Score</div>
        </div>
        <div class="stat">
          <div class="stat-val">{iterations}</div>
          <div class="stat-lbl">Iterations</div>
        </div>
        <div class="stat">
          <div class="stat-val">{thresh_pct}</div>
          <div class="stat-lbl">Threshold</div>
        </div>
      </div>
      <div class="ema-note">Smoothed EMA: {ema_pct}</div>
    </div>
    <div class="footer">
      <div class="verified">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5"><path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
        Verified on SettleBridge
        {f'<span class="merkle">{merkle_short}</span>' if merkle_short else ''}
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        {f'<span style="font-size:11px;color:#94a3b8">{date_str}</span>' if date_str else ''}
        <span class="status-chip" style="background:{status_color}">{status_label}</span>
      </div>
    </div>
    {fs_html}
  </div>
</body>
</html>"""


def _transcript_response(t: TrainingTranscript) -> TranscriptResponse:
    return TranscriptResponse(
        id=t.id,
        training_run_id=t.training_run_id,
        agent_id=t.agent_display_id,
        bounty_id=t.bounty_id,
        total_iterations=t.total_iterations,
        total_stake_spent=t.total_stake_spent,
        final_training_ema=t.final_training_ema,
        merkle_root=t.merkle_root,
        signed_payload=t.signed_payload,
        generated_at=t.generated_at,
    )


def _score_row_response(r: ScoreHistory) -> ScoreHistoryRow:
    return ScoreHistoryRow(
        id=r.id,
        agent_user_id=r.agent_user_id,
        bounty_id=r.bounty_id,
        training_run_id=r.training_run_id,
        task_type=r.task_type,
        numeric_score=r.numeric_score,
        reasoning=r.reasoning,
        diagnostics=r.diagnostics,
        mode=r.mode.value,
        provenance_hash=r.provenance_hash,
        created_at=r.created_at,
    )
