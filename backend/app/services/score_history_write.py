"""Score-history write-path contract. No database imports.

Remaps stay stable for existing agents: issues → actionable_gaps,
notes → reasoning, score → numeric_score. The full ``ai_review`` object is
also persisted so recommendation, holdback, holdback_percent,
efficacy_criteria, and unknown keys survive the write.
"""

from __future__ import annotations

from typing import Any


def mediator_result_from_ai_review(ai_review: dict[str, Any]) -> dict[str, Any]:
    """Synthetic mediator result for virtual-escrow fallback."""
    raw_score = ai_review.get("score", 0)
    try:
        confidence = round(float(raw_score or 0) / 100.0, 4)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "confidence": confidence,
        "reasoning": ai_review.get("notes", ""),
        "structured_diagnostic": {
            "actionable_gaps": ai_review.get("issues", []),
            "details": {"source": "ai_review", "raw_score": raw_score},
            "ai_review": dict(ai_review),
        },
        "verdict": {},
        "_raw": {"source": "ai_review_fallback"},
    }


def score_history_diagnostics(
    mediator_result: dict[str, Any],
    *,
    submission_id: str,
    task_type: str | None = None,
    ai_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the diagnostics blob persisted on a score-history row."""
    structured = mediator_result.get("structured_diagnostic") or {}
    diagnostics: dict[str, Any] = {}
    if structured:
        diagnostics["actionable_gaps"] = structured.get("actionable_gaps", [])
        diagnostics["details"] = structured.get("details", {})
        if task_type is not None:
            diagnostics["task_type"] = task_type
    diagnostics["raw"] = structured or {}
    diagnostics["_submission_id"] = str(submission_id)

    full_review = ai_review if isinstance(ai_review, dict) and ai_review else None
    if full_review is None and isinstance(structured.get("ai_review"), dict):
        full_review = structured["ai_review"]
    if full_review:
        diagnostics["ai_review"] = dict(full_review)
    return diagnostics
