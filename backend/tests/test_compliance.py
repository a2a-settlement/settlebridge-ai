"""Deterministic JSON Schema compliance, including the NVDA production case."""

from __future__ import annotations

import json

from app.services.compliance import SCHEMA_VERSION, check_compliance, is_compliant_for_approval

NVDA_SCHEMA = {
    "type": "object",
    "required": [
        "ticker",
        "forecast_horizon_days",
        "last_close",
        "forecast_bands",
        "model_weights",
        "generated_by",
    ],
    "properties": {
        "ticker": {"type": "string"},
        "forecast_horizon_days": {"type": "integer"},
        "last_close": {"type": "number"},
        "model_weights": {"type": "object"},
        "generated_by": {"type": "string"},
        "forecast_bands": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["p10", "p25", "p50", "p75", "p90"],
                "properties": {
                    "p10": {"type": "number"},
                    "p25": {"type": "number"},
                    "p50": {"type": "number"},
                    "p75": {"type": "number"},
                    "p90": {"type": "number"},
                },
            },
        },
    },
}

NVDA_CRITERIA = {
    "description": "JSON with six required keys and p10/p25/p50/p75/p90 bands",
    "output_format": "json",
    "custom_checks": [NVDA_SCHEMA],
}


def _deliverable(payload: dict) -> dict:
    return {"content": json.dumps(payload), "content_type": "application/json"}


def test_unchecked_when_no_custom_checks():
    result = check_compliance({"content": "{}"}, {"output_format": "json"})
    assert result["checked"] is False
    assert result["compliant"] is False
    assert result["failures"] == []
    assert result["schema_version"] == SCHEMA_VERSION
    assert is_compliant_for_approval(result) is True
    assert is_compliant_for_approval(None) is True


def test_invalid_json_is_a_failure():
    result = check_compliance(
        {"content": "not-json"},
        {"output_format": "json", "custom_checks": [NVDA_SCHEMA]},
    )
    assert result["checked"] is True
    assert result["compliant"] is False
    assert any("not valid JSON" in f for f in result["failures"])
    assert is_compliant_for_approval(result) is False


def test_nvda_incomplete_four_of_six_keys_and_mean_instead_of_p50():
    """Production bug: 4 of 6 keys, bands used mean instead of p50."""
    payload = {
        "ticker": "NVDA",
        "forecast_horizon_days": 22,
        "last_close": 180.5,
        "forecast_bands": [
            {"mean": 182.0, "p10": 170.0, "p25": 175.0, "p75": 190.0, "p90": 200.0}
        ],
        "model_weights": {"ensemble_a": 0.5, "ensemble_b": 0.5},
    }
    result = check_compliance(_deliverable(payload), NVDA_CRITERIA)
    assert result["checked"] is True
    assert result["compliant"] is False
    assert "missing required key: generated_by" in result["failures"]
    assert any("p50" in f for f in result["failures"])


def test_nvda_complete_passes():
    payload = {
        "ticker": "NVDA",
        "forecast_horizon_days": 22,
        "last_close": 180.5,
        "forecast_bands": [
            {"p10": 170.0, "p25": 175.0, "p50": 181.0, "p75": 190.0, "p90": 200.0}
        ],
        "model_weights": {"ensemble_a": 0.5, "ensemble_b": 0.5},
        "generated_by": "alphasignal-ensemble-v1",
    }
    result = check_compliance(_deliverable(payload), NVDA_CRITERIA)
    assert result["checked"] is True
    assert result["compliant"] is True
    assert result["failures"] == []
    assert is_compliant_for_approval(result) is True


def test_invalid_schema_is_recorded_not_raised():
    result = check_compliance(
        _deliverable({"x": 1}),
        {"custom_checks": [{"type": "not-a-real-type"}]},
    )
    assert result["checked"] is True
    assert result["compliant"] is False
    assert any("invalid custom_check schema" in f for f in result["failures"])


def test_already_parsed_content_is_accepted():
    result = check_compliance(
        {"content": {"risk_factors": [1, 2, 3, 4, 5]}},
        {
            "custom_checks": [
                {
                    "type": "object",
                    "required": ["risk_factors"],
                    "properties": {"risk_factors": {"type": "array", "minItems": 5}},
                }
            ]
        },
    )
    assert result["compliant"] is True
