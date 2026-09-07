"""Quality prompt omits report history and prior-review bonus."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.review_service import (
    QUALITY_PROMPT_VERSION,
    REVIEW_MODEL,
    REVIEW_SYSTEM,
    _build_prompt,
    parse_review_response,
    quality_deliverable_text,
    review_deliverable,
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


def test_quality_prompt_version_is_content_v3():
    assert QUALITY_PROMPT_VERSION == "content-v3"
    assert REVIEW_MODEL == "claude-haiku-4-5-20251001"


def test_parse_review_response_clamps_score():
    parsed = parse_review_response(
        '```json\n{"score": 140, "recommendation": "approve", "holdback": false, "notes": "ok"}\n```'
    )
    assert parsed["score"] == 100
    assert parse_review_response("not json") == {}


def test_review_system_allows_future_expiry_and_scheduled_events():
    assert "future dates beyond today" not in REVIEW_SYSTEM
    assert "scan_timestamp" in REVIEW_SYSTEM
    assert "already occurred" in REVIEW_SYSTEM
    assert "scheduled future events are valid" in REVIEW_SYSTEM
    assert "not_after after scan_timestamp" in REVIEW_SYSTEM
    assert "valid unexpired cert" in REVIEW_SYSTEM


def test_review_system_drops_numerical_contradiction_example():
    assert "14 wildcard" not in REVIEW_SYSTEM
    assert "only 2 shown" not in REVIEW_SYSTEM
    assert "wildcard certs" not in REVIEW_SYSTEM


def test_review_system_requires_quoted_submitted_values():
    assert "quote the exact conflicting values" in REVIEW_SYSTEM
    assert "cited field" in REVIEW_SYSTEM
    assert "omit the objection" in REVIEW_SYSTEM


def test_review_system_assesses_description_requirements():
    assert "bounty title, description, and" in REVIEW_SYSTEM
    assert "Description-stated requirements count" in REVIEW_SYSTEM


def test_review_system_is_passive_without_automatic_sufficiency():
    assert "Do not require active probing" in REVIEW_SYSTEM
    assert "Do not treat any source as automatically sufficient" in REVIEW_SYSTEM
    assert "automatically sufficient to satisfy" in REVIEW_SYSTEM
    assert "Shodan" not in REVIEW_SYSTEM
    assert "InternetDB" not in REVIEW_SYSTEM
    assert "crt.sh" not in REVIEW_SYSTEM


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
    assert "title, description, and acceptance criteria" in prompt


def _fake_anthropic_client(payload: dict) -> MagicMock:
    class _Block:
        text = json.dumps(payload)

    class _Response:
        content = [_Block()]

    client = MagicMock()
    client.messages.create = AsyncMock(return_value=_Response())
    return client


def test_successful_review_records_prompt_version_and_model():
    payload = {
        "score": 72,
        "recommendation": "partial_approve",
        "holdback": True,
        "notes": "Solid structure; holdback for live CT URLs.",
    }
    fake = _fake_anthropic_client(payload)
    with (
        patch("app.services.review_service.settings.ANTHROPIC_API_KEY", "sk-test"),
        patch("app.services.review_service.anthropic.AsyncAnthropic", return_value=fake),
    ):
        result = asyncio.run(
            review_deliverable(
                bounty_title="Recon",
                bounty_description="Passive recon",
                acceptance_criteria=None,
                reward_amount=150,
                difficulty="medium",
                deliverable_content=json.dumps(_report()),
                provenance=None,
            )
        )
    assert result["score"] == 72
    assert result["model"] == REVIEW_MODEL
    assert result["quality_prompt_version"] == "content-v3"
    assert result["quality_prompt_version"] == QUALITY_PROMPT_VERSION
    fake.messages.create.assert_awaited_once()


def test_failed_review_does_not_stamp_version():
    with patch("app.services.review_service.settings.ANTHROPIC_API_KEY", ""):
        result = asyncio.run(
            review_deliverable(
                bounty_title="Recon",
                bounty_description="Passive recon",
                acceptance_criteria=None,
                reward_amount=150,
                difficulty="medium",
                deliverable_content="{}",
            )
        )
    assert result == {}
    assert "quality_prompt_version" not in result
    assert "model" not in result
