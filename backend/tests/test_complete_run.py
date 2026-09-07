"""complete_run approval gates — no database required."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.training_gates import apply_completion_resolution, winner_may_auto_approve


class SubmissionStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PARTIALLY_APPROVED = "partially_approved"


class ClaimStatus(str, enum.Enum):
    ACTIVE = "active"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

NOW = datetime(2026, 9, 7, 19, 0, tzinfo=timezone.utc)


def _sub(
    *,
    rec: str = "approve",
    score: int = 92,
    compliance: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        ai_review={"recommendation": rec, "score": score},
        compliance=compliance,
        status=SubmissionStatus.PENDING_REVIEW,
        reviewer_notes=None,
        reviewed_at=None,
    )


def _claim() -> SimpleNamespace:
    return SimpleNamespace(status=ClaimStatus.ACTIVE, resolved_at=None)


def test_all_reject_does_not_approve():
    ok, reason = winner_may_auto_approve(
        recommendation="reject",
        score=15,
        score_threshold=0.7,
        auto_approve=True,
        compliance={"checked": False, "compliant": False},
    )
    assert ok is False
    assert reason == "recommendation_reject"

    sub, claim = _sub(rec="reject", score=15), _claim()
    action = apply_completion_resolution(
        sub, claim, is_winner=True, auto_approve=True, score_threshold=0.7, now=NOW
    )
    assert action.startswith("left_pending")
    assert sub.status == SubmissionStatus.PENDING_REVIEW
    assert claim.status == ClaimStatus.ACTIVE


def test_below_threshold_does_not_approve():
    ok, reason = winner_may_auto_approve(
        recommendation="approve",
        score=15,
        score_threshold=0.7,
        auto_approve=True,
        compliance=None,
    )
    assert ok is False
    assert reason == "below_threshold"


def test_auto_approve_false_is_respected():
    ok, reason = winner_may_auto_approve(
        recommendation="approve",
        score=92,
        score_threshold=0.5,
        auto_approve=False,
        compliance={"checked": True, "compliant": True},
    )
    assert ok is False
    assert reason == "auto_approve_disabled"

    sub, claim = _sub(), _claim()
    action = apply_completion_resolution(
        sub, claim, is_winner=True, auto_approve=False, score_threshold=0.5, now=NOW
    )
    assert action == "left_pending"
    assert sub.status == SubmissionStatus.PENDING_REVIEW
    assert sub.reviewed_at is None


def test_non_compliant_winner_is_not_approved():
    compliance = {
        "checked": True,
        "compliant": False,
        "failures": ["missing required key: generated_by"],
    }
    ok, reason = winner_may_auto_approve(
        recommendation="approve",
        score=92,
        score_threshold=0.5,
        auto_approve=True,
        compliance=compliance,
    )
    assert ok is False
    assert reason == "not_compliant"

    sub, claim = _sub(compliance=compliance), _claim()
    action = apply_completion_resolution(
        sub, claim, is_winner=True, auto_approve=True, score_threshold=0.5, now=NOW
    )
    assert action == "left_pending:not_compliant"
    assert sub.status == SubmissionStatus.PENDING_REVIEW


def test_unchecked_compliance_does_not_block():
    ok, reason = winner_may_auto_approve(
        recommendation="approve",
        score=80,
        score_threshold=0.5,
        auto_approve=True,
        compliance={"checked": False, "compliant": False},
    )
    assert ok is True
    assert reason == "ok"


def test_passing_winner_is_approved():
    sub, claim = _sub(score=80, compliance={"checked": True, "compliant": True}), _claim()
    action = apply_completion_resolution(
        sub, claim, is_winner=True, auto_approve=True, score_threshold=0.5, now=NOW
    )
    assert action == "approved"
    assert sub.status == SubmissionStatus.APPROVED
    assert claim.status == ClaimStatus.ACCEPTED
    assert sub.reviewed_at == NOW


def test_non_winner_rejected_on_auto_approve():
    sub, claim = _sub(rec="reject", score=10), _claim()
    action = apply_completion_resolution(
        sub, claim, is_winner=False, auto_approve=True, score_threshold=0.5, now=NOW
    )
    assert action == "rejected"
    assert sub.status == SubmissionStatus.REJECTED
    assert claim.status == ClaimStatus.REJECTED


def test_tie_break_prefers_earlier_submission():
    """Server order is score DESC, submitted_at ASC — earlier wins ties."""
    earlier = datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)
    later = datetime(2026, 9, 7, 18, 30, tzinfo=timezone.utc)
    pending = [
        SimpleNamespace(score=92, submitted_at=later, id="later"),
        SimpleNamespace(score=92, submitted_at=earlier, id="earlier"),
        SimpleNamespace(score=80, submitted_at=earlier, id="lower"),
    ]
    ordered = sorted(pending, key=lambda s: (-s.score, s.submitted_at))
    assert [s.id for s in ordered] == ["earlier", "later", "lower"]
