"""Quality prompt omits report history and prior-review bonus."""

from __future__ import annotations

import json

from app.services.review_service import (
    QUALITY_PROMPT_VERSION,
    _build_prompt,
    quality_deliverable_text,
    strip_report_history,
)


def _report() -> dict:
    return {
        "findings": [{"title": "Subdomain enumeration", "raw_data": {"verified_count": 22}}],
        "attack_narrative": "Unrelated narrative stays.",
        "iteration_delta": {
            "changes": "Applied align_counts:expired_certs:15; unresolved: HIGH: DNS evidence",
            "prior_score": 0.72,
        },
        "prior_iterations": [{"role": "baseline_edited", "score": 0.72}],
    }


def test_quality_prompt_version_is_content_v1():
    assert QUALITY_PROMPT_VERSION == "content-v1"


def test_json_history_stripped_at_report_level():
    report = _report()
    stripped = strip_report_history(report)
    assert "iteration_delta" not in stripped
    assert "prior_iterations" not in stripped
    assert stripped["findings"] == report["findings"]
    assert stripped["attack_narrative"] == report["attack_narrative"]


def test_nested_content_object_history_stripped():
    body = {"content": _report(), "format": "application/json"}
    stripped = strip_report_history(body)
    assert stripped["format"] == "application/json"
    assert "iteration_delta" not in stripped["content"]
    assert stripped["content"]["findings"] == body["content"]["findings"]


def test_non_json_passthrough():
    raw = "plain text deliverable with iteration_delta mention"
    assert quality_deliverable_text(raw) == raw


def test_unrelated_content_survives_quality_text():
    report = _report()
    text = quality_deliverable_text(json.dumps(report))
    parsed = json.loads(text)
    assert parsed["findings"] == report["findings"]
    assert parsed["attack_narrative"] == report["attack_narrative"]
    assert "iteration_delta" not in parsed
    assert "prior_score" not in text


def test_build_prompt_has_no_prior_bonus_or_history():
    report = _report()
    prompt = _build_prompt(
        bounty_title="Recon",
        bounty_description="No refinement credit.",
        acceptance_criteria=None,
        reward_amount=150,
        difficulty="medium",
        deliverable_content=json.dumps(report),
        provenance=None,
    )
    assert "Subdomain enumeration" in prompt
    assert "Unrelated narrative stays." in prompt
    assert "+10" not in prompt
    assert "self-improvement" not in prompt
    assert "Prior Submission History" not in prompt
    assert "prior_score" not in prompt
    assert "unresolved: HIGH" not in prompt
