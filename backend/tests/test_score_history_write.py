"""Unit tests for the score-history write-path contract (no DB)."""

from __future__ import annotations

from app.services.score_history_write import (
    mediator_result_from_ai_review,
    score_history_diagnostics,
)

_AI_REVIEW = {
    "score": 72,
    "notes": "Holdback for unverifiable paste URLs.",
    "issues": ["Paste evidence is suggestive, not conclusive."],
    "recommendation": "partial_approve",
    "holdback": True,
    "holdback_percent": 20,
    "efficacy_criteria": "Spot-check the two pastebin URLs.",
    "extra_opaque": {"source": "quality-v1"},
}


def test_fallback_keeps_remaps_and_attaches_full_ai_review():
    result = mediator_result_from_ai_review(_AI_REVIEW)
    assert result["confidence"] == 0.72
    assert result["reasoning"] == _AI_REVIEW["notes"]
    assert result["structured_diagnostic"]["actionable_gaps"] == _AI_REVIEW["issues"]
    assert result["structured_diagnostic"]["details"]["source"] == "ai_review"
    assert result["structured_diagnostic"]["ai_review"] == _AI_REVIEW
    assert result["verdict"] == {}
    assert result["_raw"]["source"] == "ai_review_fallback"


def test_written_diagnostics_include_full_ai_review_and_remaps():
    result = mediator_result_from_ai_review(_AI_REVIEW)
    diagnostics = score_history_diagnostics(
        result,
        submission_id="sub-1",
        task_type="recon",
        ai_review=_AI_REVIEW,
    )
    assert diagnostics["actionable_gaps"] == _AI_REVIEW["issues"]
    assert diagnostics["_submission_id"] == "sub-1"
    assert diagnostics["task_type"] == "recon"
    assert diagnostics["details"]["source"] == "ai_review"
    stored = diagnostics["ai_review"]
    assert stored["recommendation"] == "partial_approve"
    assert stored["holdback"] is True
    assert stored["holdback_percent"] == 20
    assert stored["efficacy_criteria"] == _AI_REVIEW["efficacy_criteria"]
    assert stored["notes"] == _AI_REVIEW["notes"]
    assert stored["issues"] == _AI_REVIEW["issues"]
    assert stored["score"] == 72
    assert stored["extra_opaque"] == {"source": "quality-v1"}


def test_written_diagnostics_use_structured_ai_review_when_submission_lacks_it():
    result = mediator_result_from_ai_review(_AI_REVIEW)
    diagnostics = score_history_diagnostics(
        result,
        submission_id="sub-2",
        ai_review=None,
    )
    assert diagnostics["ai_review"]["recommendation"] == "partial_approve"
    assert diagnostics["ai_review"]["holdback_percent"] == 20
    assert diagnostics["actionable_gaps"] == _AI_REVIEW["issues"]


def test_compliance_failures_union_into_gaps_when_llm_reports_none():
    result = mediator_result_from_ai_review(
        {**_AI_REVIEW, "issues": [], "score": 92, "recommendation": "approve"}
    )
    compliance = {
        "checked": True,
        "compliant": False,
        "failures": ["missing required key: generated_by", "forecast_bands[0]: missing required key: p50"],
    }
    diagnostics = score_history_diagnostics(
        result,
        submission_id="sub-nvda",
        task_type="forecast",
        ai_review={**_AI_REVIEW, "issues": []},
        compliance=compliance,
    )
    assert "missing required key: generated_by" in diagnostics["actionable_gaps"]
    assert any("p50" in g for g in diagnostics["actionable_gaps"])
    assert diagnostics["compliance"]["compliant"] is False


def test_written_diagnostics_without_ai_review_keep_legacy_shape():
    mediator_result = {
        "confidence": 0.5,
        "reasoning": "mediator only",
        "structured_diagnostic": {
            "actionable_gaps": ["gap"],
            "details": {"source": "mediator"},
        },
    }
    diagnostics = score_history_diagnostics(
        mediator_result,
        submission_id="sub-3",
        task_type="recon",
    )
    assert "ai_review" not in diagnostics
    assert diagnostics["actionable_gaps"] == ["gap"]
    assert diagnostics["details"]["source"] == "mediator"
    assert diagnostics["_submission_id"] == "sub-3"
