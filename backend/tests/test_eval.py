"""Golden-set labels, Brier, and the frozen-champion promotion rule."""

from __future__ import annotations

from app.services.review_service import QUALITY_PROMPT_VERSION, REVIEW_MODEL
from eval.calibration import brier_score, compare_to_baselines, evaluate_rows, naive_brier
from eval.golden import labeled_fixtures, scored_rows
from eval.promotion import load_champion, may_promote


def test_fixture_labels_match_the_checker():
    rows = labeled_fixtures()
    ids = {r["id"] for r in rows}
    assert ids == {"baseline", "incomplete", "complete"}
    assert all(r["label_matches_checker"] for r in rows)
    by_id = {r["id"]: r for r in rows}
    assert by_id["baseline"]["label_compliant"] is False
    assert by_id["incomplete"]["label_compliant"] is False
    assert by_id["complete"]["label_compliant"] is True


def test_brier_and_naive_on_identical_set():
    rows = [
        {"score": 92, "compliant": False},
        {"score": 10, "compliant": False},
        {"score": 88, "compliant": True},
    ]
    result = evaluate_rows(rows)
    expected = brier_score([0.92, 0.10, 0.88], [0, 0, 1])
    assert result["brier"] == expected
    assert result["naive_brier"] == naive_brier([0, 0, 1])
    assert result["n"] == 3
    assert len(result["reliability"]) == 10
    decile_9 = result["reliability"][9]
    assert decile_9["n"] == 1
    assert decile_9["observed_rate"] == 0.0


def test_observed_incomplete_score_matches_champion_brier():
    rows = scored_rows()
    assert rows == [{"id": "incomplete", "score": 92.0, "compliant": False}]
    result = evaluate_rows(rows)
    champion = load_champion()
    assert abs(result["brier"] - champion["brier"]) < 1e-6


def test_promotion_requires_beating_champion_and_naive():
    assert may_promote(0.10, 0.20, 0.15) is True
    assert may_promote(0.18, 0.20, 0.15) is False
    assert may_promote(0.20, 0.20, 0.30) is False
    gate = compare_to_baselines(0.10, 0.20, 0.15)
    assert gate["may_promote"] is True
    assert gate["beats_champion"] is True
    assert gate["beats_naive"] is True


def test_frozen_champion_is_content_v3():
    champion = load_champion()
    assert champion["quality_prompt_version"] == "content-v3"
    assert champion["quality_prompt_version"] == QUALITY_PROMPT_VERSION
    assert champion["review_model"] == REVIEW_MODEL
    assert QUALITY_PROMPT_VERSION == "content-v3"
