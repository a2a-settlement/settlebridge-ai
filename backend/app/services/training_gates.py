"""Approval gates for training-run completion. No database imports."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def winner_may_auto_approve(
    *,
    recommendation: str | None,
    score: Any,
    score_threshold: float,
    auto_approve: bool,
    compliance: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Gate the complete_run winner. ``score`` is the 0–100 AI review score."""
    if not auto_approve:
        return False, "auto_approve_disabled"
    rec = (recommendation or "reject").strip().lower()
    if rec == "reject":
        return False, "recommendation_reject"
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return False, "missing_score"
    if numeric / 100.0 < float(score_threshold):
        return False, "below_threshold"
    if isinstance(compliance, dict) and compliance.get("checked") and not compliance.get(
        "compliant"
    ):
        return False, "not_compliant"
    return True, "ok"


def apply_completion_resolution(
    sub: Any,
    claim: Any,
    *,
    is_winner: bool,
    auto_approve: bool,
    score_threshold: float,
    now: datetime,
) -> str:
    """Mutate submission/claim for run completion. Returns the action taken.

    Status values are taken from ``type(sub.status)`` / ``type(claim.status)``
    so this module never imports the ORM.
    """
    if not auto_approve:
        return "left_pending"
    rec = (sub.ai_review or {}).get("recommendation", "reject")
    sub_status = type(sub.status)
    claim_status = type(claim.status)
    if is_winner:
        ok, reason = winner_may_auto_approve(
            recommendation=rec,
            score=(sub.ai_review or {}).get("score"),
            score_threshold=score_threshold,
            auto_approve=True,
            compliance=sub.compliance if isinstance(getattr(sub, "compliance", None), dict) else None,
        )
        if not ok:
            return f"left_pending:{reason}"
        sub.reviewed_at = now
        sub.status = sub_status.APPROVED
        sub.reviewer_notes = "[Auto-resolved at run completion — highest scoring submission]"
        claim.status = claim_status.ACCEPTED
        claim.resolved_at = now
        return "approved"
    sub.reviewed_at = now
    if rec == "partial_approve":
        sub.status = sub_status.PARTIALLY_APPROVED
        sub.reviewer_notes = "[Auto-resolved at run completion]"
        claim.status = claim_status.ACCEPTED
        claim.resolved_at = now
        return "partial_approve"
    sub.status = sub_status.REJECTED
    sub.reviewer_notes = "[Auto-resolved at run completion — lower scoring submission]"
    claim.status = claim_status.REJECTED
    claim.resolved_at = now
    return "rejected"
