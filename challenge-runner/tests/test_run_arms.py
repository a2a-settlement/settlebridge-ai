"""Pure helpers for the corpus arms runner. No model calls and no checkouts."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from corpus.harness import _over_ten_percent

_PATH = Path(__file__).resolve().parents[1] / "corpus" / "tools" / "run_arms.py"
_SPEC = importlib.util.spec_from_file_location("run_arms", _PATH)
arms = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(arms)


def test_band_matches_harness_rule():
    for reference in range(0, 80):
        for total in range(0, 120):
            assert arms.in_band(total, reference) == (not _over_ten_percent(total, reference))


def test_exact_ten_percent_is_inside():
    assert arms.in_band(90, 100)
    assert arms.in_band(110, 100)
    assert not arms.in_band(89, 100)
    assert not arms.in_band(111, 100)


def test_combine_claim_verdicts_prefers_confirmed():
    assert arms.combine_claim_verdicts([]) == "no_failure_found"
    assert (
        arms.combine_claim_verdicts(["no_failure_found", "confirmed", "inconclusive"])
        == "confirmed"
    )
    assert arms.combine_claim_verdicts(["invalid_or_duplicate", "inconclusive"]) == "inconclusive"


def test_parse_review_verdict():
    assert arms.parse_review_verdict('{"verdict":"confirmed","reason":"x"}') == "confirmed"
    fenced = '```json\n{"verdict":"no_failure_found","reason":"x"}\n```'
    assert arms.parse_review_verdict(fenced) == "no_failure_found"
    assert arms.parse_review_verdict("not json") == "inconclusive"
    assert arms.parse_review_verdict('{"verdict":"invalid_or_duplicate"}') == "inconclusive"


def test_pytest_outcome_uses_the_footer_counts():
    errors = "============================== 76 errors in 0.18s =============================="
    assert arms._pytest_outcome(errors, 1) == (2, "passed=0 failed=0 errors=76")
    mixed = "==== 2 failed, 3 passed, 1 error in 1.00s ===="
    assert arms._pytest_outcome(mixed, 1)[0] == 1
    passed = "==== 4 passed in 0.20s ===="
    assert arms._pytest_outcome(passed, 0) == (0, "passed=4 failed=0 errors=0")


def test_baseline_verdict_differential():
    assert arms.baseline_verdict(1, 0, False) == ("confirmed", True)
    assert arms.baseline_verdict(0, 0, False) == ("no_failure_found", True)
    assert arms.baseline_verdict(1, 1, False) == ("no_failure_found", True)
    assert arms.baseline_verdict(0, 0, True) == ("confirmed", True)
    assert arms.baseline_verdict(124, 0, False) == ("inconclusive", False)
    assert arms.baseline_verdict(1, None, False) == ("inconclusive", True)
    assert arms.baseline_verdict(0, None, False) == ("no_failure_found", True)
    assert arms.baseline_verdict(5, None, False) == ("inconclusive", True)
    assert arms.baseline_verdict(5, 5, False) == ("inconclusive", True)


def _row(entry_id: str, cents: int, verdict: str = "no_failure_found") -> dict:
    return {
        "entry_id": entry_id,
        "arm": "tests_and_scanners",
        "verdict": verdict,
        "audit": None,
        "controls_ok": True,
        "oracle_tamper_accepted": False,
        "compute_cents": cents,
    }


def test_top_up_lands_inside_the_band():
    rows = [_row("a", 40)]
    calls = []

    def again(entry_id: str):
        calls.append(entry_id)
        return 30, 0.0, 1.0, "no_failure_found", True

    budget = {"spent": 0.0}
    arms.top_up(rows, 100, {"a": 30}, again, budget, cap=5000)
    assert arms.in_band(rows[0]["compute_cents"], 100)
    assert rows[0]["compute_cents"] == 100
    assert calls == ["a", "a"]


def test_top_up_stops_when_one_more_unit_would_leave_the_band():
    rows = [_row("a", 40)]

    def again(entry_id: str):
        raise AssertionError("unit does not fit")

    budget = {"spent": 0.0}
    arms.top_up(rows, 100, {"a": 80}, again, budget, cap=5000)
    assert rows[0]["compute_cents"] == 40
    assert not arms.in_band(40, 100)


def test_top_up_disagreement_is_inconclusive():
    rows = [_row("a", 40, "no_failure_found")]

    def again(entry_id: str):
        return 30, 0.0, 1.0, "confirmed", True

    budget = {"spent": 0.0}
    arms.top_up(rows, 100, {"a": 30}, again, budget, cap=5000)
    assert rows[0]["verdict"] == "inconclusive"


def test_top_up_stops_at_the_model_cap():
    rows = [_row("a", 10)]
    calls = []

    def again(entry_id: str):
        calls.append(entry_id)
        return 10, 25.0, 1.0, "no_failure_found", True

    budget = {"spent": 0.0}
    arms.top_up(rows, 100, {"a": 10}, again, budget, cap=30)
    assert len(calls) == 2
    assert budget["spent"] == 50.0
    assert budget["spent"] > 30
