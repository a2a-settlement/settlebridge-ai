"""Unit tests for skill track-record aggregation (no DB)."""

from __future__ import annotations

from app.services.skill_evidence import (
    aggregate_skill_evidence,
    effective_score,
    extract_skill_ids,
)


def test_effective_score_prefers_submission_score():
    assert effective_score(90, {"score": 50}) == 90.0
    assert effective_score(None, {"score": 84}) == 84.0
    assert effective_score(None, {"score": "77"}) == 77.0
    assert effective_score(None, None) is None
    assert effective_score(None, {"notes": "x"}) is None


def test_extract_skill_ids_prefers_card():
    account = {
        "skills": ["legacy-tag"],
        "agent_card": {
            "skills": [
                {"id": "ensemble-prediction", "name": "Ensemble"},
                {"id": "sec-filings"},
            ]
        },
    }
    assert extract_skill_ids(account) == ["ensemble-prediction", "sec-filings"]


def test_extract_skill_ids_falls_back_to_account_skills():
    assert extract_skill_ids({"skills": ["a", "b"], "agent_card": None}) == ["a", "b"]


def test_aggregate_skill_evidence_exact_tag_match_and_badge():
    skill_ids = ["ensemble-prediction", "sec-filings"]
    rows = [
        (["ensemble-prediction", "finance"], 80.0),
        (["ensemble-prediction"], 90.0),
        (["ensemble-prediction"], 85.0),
        (["ensemble-prediction"], 70.0),
        (["ensemble-prediction"], 88.0),
        (["other"], 99.0),
        (["sec-filings"], 60.0),
    ]
    evidence = aggregate_skill_evidence(skill_ids, rows)
    by_id = {e["skill_id"]: e for e in evidence}

    ens = by_id["ensemble-prediction"]
    assert ens["settled_count"] == 5
    assert ens["avg_score"] == 82.6
    assert ens["evidenced"] is True

    sec = by_id["sec-filings"]
    assert sec["settled_count"] == 1
    assert sec["avg_score"] == 60.0
    assert sec["evidenced"] is False


def test_aggregate_no_history():
    evidence = aggregate_skill_evidence(["x"], [])
    assert evidence == [
        {"skill_id": "x", "settled_count": 0, "avg_score": None, "evidenced": False}
    ]
