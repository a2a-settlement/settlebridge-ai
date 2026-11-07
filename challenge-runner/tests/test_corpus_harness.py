"""Corpus structure checks and hand-computed harness metrics."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from corpus.harness import (
    ENTRY_FIELDS,
    UNVERIFIED_BANNER,
    VERIFICATION_FIELDS,
    CorpusError,
    format_measured,
    format_text,
    load_manifest,
    load_results,
    main,
    score,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
SB_PROPERTY = (
    "A bounty on a private repository is visible and claimable only by a "
    "signed-in user whose linked GitHub account has read access."
)


def _banned() -> str:
    return "sec" + "ure"


def _entry(
    entry_id: str,
    category: str,
    *,
    project: str | None = None,
    verified: bool = True,
) -> dict:
    failure = "regression" if category == "regression" else "integrity"
    return {
        "id": entry_id,
        "project": project or f"ex/{entry_id}",
        "ecosystem": "pypi",
        "advisory_ids": ["CVE-2024-1000"],
        "category": category,
        "fix_commit": None,
        "base_commit": None,
        "followup_fix_commit": None,
        "failure_class": failure,
        "property": (
            f"Entry {entry_id} does not accept an input that violates its stated boundary."
        ),
        "oracle_source": "synthetic fixture",
        "notes": "synthetic fixture",
        "held_out": True,
        "verified": verified,
        "verification": {"checked_at": None, "checked_by": None, "evidence_urls": []},
    }


def _result(
    entry_id: str,
    arm: str,
    verdict: str,
    *,
    audit: str | None = None,
    controls: bool = True,
    tamper: bool = False,
    cents: int = 100,
) -> dict:
    return {
        "entry_id": entry_id,
        "arm": arm,
        "verdict": verdict,
        "audit": audit,
        "controls_ok": controls,
        "oracle_tamper_accepted": tamper,
        "compute_cents": cents,
    }


def _row(results: list[dict], entry_id: str, arm: str) -> dict:
    for row in results:
        if row["entry_id"] == entry_id and row["arm"] == arm:
            return row
    raise AssertionError((entry_id, arm))


def passing_bundle() -> tuple[dict, list[dict]]:
    """20 verified rows: 8 incomplete confirms, 2 inconclusive, gap of 8."""
    entries: list[dict] = []
    results: list[dict] = []
    for index in range(8):
        entry_id = f"inc{index}"
        entries.append(_entry(entry_id, "incomplete_fix", project=f"inc/p{index}"))
        results.append(
            _result(entry_id, "seeded_challenger", "confirmed", audit="agree", cents=1000)
        )
        results.append(_result(entry_id, "second_model_review", "no_failure_found", cents=460))
    for index in range(8):
        entry_id = f"comp{index}"
        entries.append(_entry(entry_id, "complete_fix", project=f"comp/p{index}"))
        verdict = "inconclusive" if index < 2 else "no_failure_found"
        results.append(_result(entry_id, "seeded_challenger", verdict, cents=100))
        results.append(_result(entry_id, "second_model_review", "no_failure_found", cents=460))
    for index in range(4):
        entry_id = f"reg{index}"
        entries.append(_entry(entry_id, "regression", project=f"reg/p{index}"))
        results.append(_result(entry_id, "seeded_challenger", "no_failure_found", cents=100))
        results.append(_result(entry_id, "second_model_review", "no_failure_found", cents=460))
    return {"schema_version": 1, "entries": entries}, results


def _failed(report: dict) -> set[str]:
    return {item["id"] for item in report["go_no_go"]["criteria"] if not item["passed"]}


def _assert_criterion_lines(report: dict) -> None:
    text = format_text(report)
    for item in report["go_no_go"]["criteria"]:
        mark = "PASS" if item["passed"] else "FAIL"
        measured = format_measured(item["measured"])
        line = f"{mark} {item['id']} measured={measured}; need {item['need']}"
        assert line in text, line


def test_manifest_loads_and_meets_minimums():
    schema = json.loads((CORPUS / "schema.json").read_text(encoding="utf-8"))
    raw = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema["$defs"]["entry"]["required"]) == set(ENTRY_FIELDS)
    assert set(schema["$defs"]["verification"]["required"]) == set(VERIFICATION_FIELDS)
    jsonschema.validate(raw, schema)

    manifest = load_manifest(CORPUS / "manifest.json")
    entries = manifest["entries"]
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids))
    projects = {entry["project"] for entry in entries}
    assert len(projects) >= 26
    counts = {category: 0 for category in ("incomplete_fix", "complete_fix", "regression")}
    held_out_false = []
    for entry in entries:
        assert entry["verified"] is False
        assert entry["ecosystem"] == "pypi"
        counts[entry["category"]] += 1
        if entry["held_out"] is False:
            held_out_false.append(entry["id"])
        if entry["id"] != "sb-pr32":
            assert entry["advisory_ids"]
    assert counts["incomplete_fix"] >= 8
    assert counts["complete_fix"] >= 8
    assert counts["regression"] >= 4
    assert held_out_false == ["sb-pr32"]
    for project in (
        "django/django",
        "pallets/flask",
        "pallets/jinja",
        "sqlalchemy/mako",
        "jpadilla/pyjwt",
        "pyca/cryptography",
        "yaml/pyyaml",
        "lxml/lxml",
        "a2a-settlement/settlebridge-ai",
    ):
        assert project in projects
    sb = next(entry for entry in entries if entry["id"] == "sb-pr32")
    assert sb["property"] == SB_PROPERTY
    assert sb["project"] == "a2a-settlement/settlebridge-ai"
    assert sb["fix_commit"] is None
    assert sb["base_commit"] is None
    assert sb["followup_fix_commit"] is None
    assert "b16a485" in sb["notes"]
    assert "520b6b6" in sb["notes"]
    assert "32" in sb["notes"]
    banned = _banned()
    for path in (
        CORPUS / "schema.json",
        CORPUS / "manifest.json",
        CORPUS / "README.md",
        CORPUS / "harness.py",
        CORPUS / "__init__.py",
    ):
        assert banned not in path.read_text(encoding="utf-8").lower(), path.name


def test_unverified_entries_are_refused_without_the_flag():
    manifest = load_manifest(CORPUS / "manifest.json")
    entry_id = manifest["entries"][0]["id"]
    results = [_result(entry_id, "seeded_challenger", "no_failure_found")]
    with pytest.raises(CorpusError, match="unverified"):
        score(manifest, results)
    report = score(manifest, results, allow_unverified=True)
    assert report["banner"] == "UNVERIFIED \u2014 not a go/no-go result"
    assert report["unverified"] is True
    assert report["go_no_go"]["authoritative"] is False
    text = format_text(report)
    assert text.startswith(UNVERIFIED_BANNER)
    assert text.rstrip().endswith(UNVERIFIED_BANNER)
    payload = json.dumps(report, ensure_ascii=False)
    assert UNVERIFIED_BANNER in payload
    assert _banned() not in text.lower()
    assert _banned() not in payload.lower()


def test_cli_refuses_then_marks_unverified_output(tmp_path, capsys):
    manifest_path = CORPUS / "manifest.json"
    entry_id = json.loads(manifest_path.read_text(encoding="utf-8"))["entries"][0]["id"]
    results_path = tmp_path / "results.jsonl"
    results_path.write_text(
        json.dumps(_result(entry_id, "seeded_challenger", "no_failure_found")) + "\n",
        encoding="utf-8",
    )
    refused = main(["--manifest", str(manifest_path), "--results", str(results_path)])
    captured = capsys.readouterr()
    assert refused == 2
    assert captured.out == ""
    assert "unverified" in captured.err.lower()
    assert "go_no_go" not in captured.out

    allowed = main(
        [
            "--manifest",
            str(manifest_path),
            "--results",
            str(results_path),
            "--allow-unverified",
        ]
    )
    captured = capsys.readouterr()
    assert allowed == 0
    assert captured.out.count(UNVERIFIED_BANNER) >= 2
    assert _banned() not in captured.out.lower()


def test_metrics_match_hand_computed_values(tmp_path):
    entries = [
        _entry("i1", "incomplete_fix"),
        _entry("i2", "incomplete_fix"),
        _entry("i3", "incomplete_fix"),
        _entry("c1", "complete_fix"),
        _entry("c2", "complete_fix"),
        _entry("r1", "regression"),
    ]
    challenger = [
        _result("i1", "seeded_challenger", "confirmed", audit="agree", cents=100),
        _result("i2", "seeded_challenger", "confirmed", audit="disagree", cents=300),
        _result("i3", "seeded_challenger", "confirmed", audit="agree", cents=700),
        _result(
            "c1",
            "seeded_challenger",
            "confirmed",
            audit="agree",
            controls=False,
            tamper=True,
            cents=500,
        ),
        _result("c2", "seeded_challenger", "inconclusive", cents=200),
        _result("r1", "seeded_challenger", "no_failure_found", cents=400),
    ]
    review = [
        _result("i1", "second_model_review", "no_failure_found", cents=300),
        _result("i2", "second_model_review", "no_failure_found", cents=300),
        _result("i3", "second_model_review", "no_failure_found", cents=300),
        _result("c1", "second_model_review", "no_failure_found", cents=300),
        _result("c2", "second_model_review", "no_failure_found", cents=400),
        _result("r1", "second_model_review", "no_failure_found", cents=400),
    ]
    scanners = [
        _result(entry_id, "tests_and_scanners", "no_failure_found", cents=400)
        for entry_id in ("i1", "i2", "i3", "c1", "c2")
    ]
    scanners.append(_result("r1", "tests_and_scanners", "no_failure_found", cents=500))
    path = tmp_path / "results.jsonl"
    lines = [json.dumps(row) for row in challenger + review + scanners]
    path.write_text("\n".join(lines) + "\n\n", encoding="utf-8")
    loaded = load_results(path)
    report = score({"schema_version": 1, "entries": entries}, loaded)
    arm = report["arms"]["seeded_challenger"]
    assert arm["confirmed_rate_incomplete"] == 1.0
    assert arm["incomplete_confirmed"] == 3
    assert arm["incomplete_scored"] == 3
    assert arm["false_confirms"] == 2
    assert arm["blind_audit_precision"] == pytest.approx(0.75)
    assert arm["audit_agree"] == 3
    assert arm["audit_disagree"] == 1
    assert arm["inconclusive_rate"] == pytest.approx(1 / 6)
    assert arm["inconclusive"] == 1
    assert arm["controls_correct_rate"] == pytest.approx(5 / 6)
    assert arm["controls_ok"] == 5
    assert arm["accepted_tamper"] == 1
    assert arm["median_compute_cents_per_confirmed"] == 400.0
    assert arm["confirmed"] == 4
    assert arm["total_compute_cents"] == 2200
    assert arm["scored"] == 6
    assert arm["by_category"] == {"incomplete_fix": 3, "complete_fix": 2, "regression": 1}

    other = report["arms"]["second_model_review"]
    assert other["confirmed_rate_incomplete"] == 0.0
    assert other["false_confirms"] == 0
    assert other["blind_audit_precision"] is None
    assert other["inconclusive_rate"] == 0.0
    assert other["controls_correct_rate"] == 1.0
    assert other["accepted_tamper"] == 0
    assert other["median_compute_cents_per_confirmed"] is None
    assert other["total_compute_cents"] == 2000

    assert report["equal_compute"]["flagged"] == ["tests_and_scanners"]
    assert report["equal_compute"]["arms"]["second_model_review"]["flagged"] is False
    assert report["equal_compute"]["arms"]["tests_and_scanners"]["rel_diff"] == round(300 / 2200, 6)
    assert report["go_no_go"]["passed"] is False
    assert report["banner"] is None
    text = format_text(report)
    assert "FAIL false_confirms measured=2; need == 0" in text
    assert "PASS median_compute_cents measured=400; need <= 2500" in text
    assert "FLAG tests_and_scanners" in text
    assert "ok   second_model_review" in text
    assert _banned() not in text.lower()


def test_go_no_go_passes_on_a_constructed_set():
    manifest, results = passing_bundle()
    report = score(manifest, results)
    assert [item["id"] for item in report["go_no_go"]["criteria"]] == [
        "incomplete_confirmed_rate",
        "blind_audit_precision",
        "false_confirms",
        "confirmations_ahead_of_second_model",
        "inconclusive_rate",
        "controls_correct_rate",
        "accepted_tamper",
        "median_compute_cents",
        "scored_entries",
        "scored_incomplete",
        "scored_complete",
        "scored_regression",
        "equal_compute_with_review",
    ]
    assert report["go_no_go"]["passed"] is True
    assert report["go_no_go"]["authoritative"] is True
    assert report["banner"] is None
    assert report["equal_compute"]["flagged"] == []
    measured = {item["id"]: item["measured"] for item in report["go_no_go"]["criteria"]}
    assert measured["incomplete_confirmed_rate"] == 1.0
    assert measured["blind_audit_precision"] == 1.0
    assert measured["false_confirms"] == 0
    assert measured["confirmations_ahead_of_second_model"] == 8
    assert measured["inconclusive_rate"] == pytest.approx(0.1)
    assert measured["controls_correct_rate"] == 1.0
    assert measured["accepted_tamper"] == 0
    assert measured["median_compute_cents"] == 1000.0
    assert measured["scored_entries"] == 20
    assert measured["scored_incomplete"] == 8
    assert measured["scored_complete"] == 8
    assert measured["scored_regression"] == 4
    text = format_text(report)
    assert "FAIL" not in text
    assert UNVERIFIED_BANNER not in text
    _assert_criterion_lines(report)


def test_threshold_edges_still_pass():
    manifest, results = passing_bundle()
    for entry_id in ("comp2", "comp3"):
        _row(results, entry_id, "seeded_challenger")["verdict"] = "inconclusive"
    report = score(manifest, results)
    inconclusive = next(
        item for item in report["go_no_go"]["criteria"] if item["id"] == "inconclusive_rate"
    )
    assert inconclusive["measured"] == pytest.approx(0.2)
    assert inconclusive["passed"] is True
    assert report["go_no_go"]["passed"] is True

    manifest, results = passing_bundle()
    _row(results, "comp4", "seeded_challenger")["controls_ok"] = False
    report = score(manifest, results)
    controls = next(
        item for item in report["go_no_go"]["criteria"] if item["id"] == "controls_correct_rate"
    )
    assert controls["measured"] == pytest.approx(0.95)
    assert controls["passed"] is True
    assert report["go_no_go"]["passed"] is True

    manifest, results = passing_bundle()
    for index in range(8):
        _row(results, f"inc{index}", "seeded_challenger")["compute_cents"] = 2500
    _balance_review(results)
    report = score(manifest, results)
    median = next(
        item for item in report["go_no_go"]["criteria"] if item["id"] == "median_compute_cents"
    )
    assert median["measured"] == 2500.0
    assert median["passed"] is True
    assert report["go_no_go"]["passed"] is True

    manifest, results = passing_bundle()
    for index in range(6):
        row = _row(results, f"inc{index}", "second_model_review")
        row["verdict"] = "confirmed"
        row["audit"] = "agree"
    report = score(manifest, results)
    ahead = next(
        item
        for item in report["go_no_go"]["criteria"]
        if item["id"] == "confirmations_ahead_of_second_model"
    )
    assert ahead["measured"] == 2
    assert ahead["passed"] is True
    assert report["go_no_go"]["passed"] is True

    manifest, results = passing_bundle()
    _row(results, "inc0", "seeded_challenger")["audit"] = "disagree"
    for entry_id in ("comp6", "comp7"):
        row = _row(results, entry_id, "seeded_challenger")
        row["verdict"] = "confirmed"
        row["audit"] = "agree"
    report = score(manifest, results)
    by_id = {item["id"]: item for item in report["go_no_go"]["criteria"]}
    assert by_id["blind_audit_precision"]["measured"] == pytest.approx(0.9)
    assert by_id["blind_audit_precision"]["passed"] is True
    assert by_id["false_confirms"]["passed"] is False


def _fail_incomplete_rate(_manifest, results):
    for index in range(5):
        row = _row(results, f"inc{index}", "seeded_challenger")
        row["verdict"] = "no_failure_found"
        row["audit"] = None


def _fail_precision(_manifest, results):
    # A disagree is also a false confirm, so both criteria fail together.
    for index in range(2):
        _row(results, f"inc{index}", "seeded_challenger")["audit"] = "disagree"


def _fail_false_confirms(_manifest, results):
    row = _row(results, "comp7", "seeded_challenger")
    row["verdict"] = "confirmed"
    row["audit"] = "agree"


def _fail_ahead(_manifest, results):
    for index in range(8):
        row = _row(results, f"inc{index}", "second_model_review")
        row["verdict"] = "confirmed"
        row["audit"] = "agree"


def _fail_inconclusive(_manifest, results):
    for entry_id in ("comp2", "comp3", "comp4"):
        row = _row(results, entry_id, "seeded_challenger")
        row["verdict"] = "inconclusive"
        row["audit"] = None


def _fail_controls(_manifest, results):
    for entry_id in ("comp2", "comp3"):
        _row(results, entry_id, "seeded_challenger")["controls_ok"] = False


def _fail_tamper(_manifest, results):
    _row(results, "reg0", "seeded_challenger")["oracle_tamper_accepted"] = True


def _fail_median(_manifest, results):
    for index in range(8):
        _row(results, f"inc{index}", "seeded_challenger")["compute_cents"] = 3000


def _drop_challenger(results, entry_ids):
    results[:] = [
        row
        for row in results
        if not (row["arm"] == "seeded_challenger" and row["entry_id"] in entry_ids)
    ]


def _fail_scored_entries(_manifest, results):
    _drop_challenger(results, {"comp7"})


def _fail_scored_incomplete(manifest, results):
    _drop_challenger(results, {f"inc{index}" for index in range(3)})
    for index in range(3):
        entry_id = f"comp{8 + index}"
        manifest["entries"].append(_entry(entry_id, "complete_fix", project=f"comp/extra{index}"))
        results.append(_result(entry_id, "seeded_challenger", "no_failure_found", cents=100))


def _fail_scored_complete(manifest, results):
    _drop_challenger(results, {"comp0", "comp1", "comp2"})
    for index in range(3):
        entry_id = f"reg{4 + index}"
        manifest["entries"].append(_entry(entry_id, "regression", project=f"reg/extra{index}"))
        results.append(_result(entry_id, "seeded_challenger", "no_failure_found", cents=100))


def _fail_scored_regression(manifest, results):
    _drop_challenger(results, {"reg0", "reg1"})
    for index in range(2):
        entry_id = f"comp{8 + index}"
        manifest["entries"].append(_entry(entry_id, "complete_fix", project=f"comp/extra{index}"))
        results.append(_result(entry_id, "seeded_challenger", "no_failure_found", cents=100))


def _balance_review(results):
    """Match review compute to the challenger so one mutation tests one criterion."""

    def total(arm):
        return sum(row["compute_cents"] for row in results if row["arm"] == arm)

    gap = total("seeded_challenger") - total("second_model_review")
    review_rows = [row for row in results if row["arm"] == "second_model_review"]
    for row in review_rows:
        step = max(gap, -row["compute_cents"])
        row["compute_cents"] += step
        gap -= step
        if gap == 0:
            break


@pytest.mark.parametrize(
    ("name", "mutate", "expected"),
    [
        ("incomplete_confirmed_rate", _fail_incomplete_rate, {"incomplete_confirmed_rate"}),
        ("blind_audit_precision", _fail_precision, {"blind_audit_precision", "false_confirms"}),
        ("false_confirms", _fail_false_confirms, {"false_confirms"}),
        (
            "confirmations_ahead_of_second_model",
            _fail_ahead,
            {"confirmations_ahead_of_second_model"},
        ),
        ("inconclusive_rate", _fail_inconclusive, {"inconclusive_rate"}),
        ("controls_correct_rate", _fail_controls, {"controls_correct_rate"}),
        ("accepted_tamper", _fail_tamper, {"accepted_tamper"}),
        ("median_compute_cents", _fail_median, {"median_compute_cents"}),
        ("scored_entries", _fail_scored_entries, {"scored_entries"}),
        ("scored_incomplete", _fail_scored_incomplete, {"scored_incomplete"}),
        ("scored_complete", _fail_scored_complete, {"scored_complete"}),
        ("scored_regression", _fail_scored_regression, {"scored_regression"}),
    ],
)
def test_each_single_violation_names_that_criterion(name, mutate, expected):
    manifest, results = passing_bundle()
    mutate(manifest, results)
    _balance_review(results)
    report = score(manifest, results)
    assert _failed(report) == expected, name
    _assert_criterion_lines(report)


def test_equal_compute_flag_uses_a_ten_percent_boundary():
    manifest = {"schema_version": 1, "entries": [_entry("only", "incomplete_fix")]}

    def run(*pairs: tuple[str, int]) -> dict:
        rows = [_result("only", arm, "no_failure_found", cents=cents) for arm, cents in pairs]
        return score(manifest, rows)

    high = run(
        ("seeded_challenger", 10000),
        ("second_model_review", 11000),
        ("tests_and_scanners", 11001),
    )
    assert high["equal_compute"]["flagged"] == ["tests_and_scanners"]
    low = run(
        ("seeded_challenger", 10000),
        ("second_model_review", 9000),
        ("tests_and_scanners", 8999),
    )
    assert low["equal_compute"]["flagged"] == ["tests_and_scanners"]
    zeros = run(("seeded_challenger", 0), ("second_model_review", 0), ("tests_and_scanners", 1))
    assert zeros["equal_compute"]["flagged"] == ["tests_and_scanners"]
    both_zero = run(("seeded_challenger", 0), ("second_model_review", 0))
    assert both_zero["equal_compute"]["flagged"] == []


def test_short_sha_and_duplicate_rows_are_rejected():
    manifest, results = passing_bundle()
    broken = json.loads(json.dumps(manifest))
    broken["entries"][0]["fix_commit"] = "b16a485"
    with pytest.raises(CorpusError, match="40 lowercase hex"):
        score(broken, [])
    duplicated = json.loads(json.dumps(manifest))
    duplicated["entries"].append(duplicated["entries"][0])
    with pytest.raises(CorpusError, match="duplicate id"):
        score(duplicated, [])
    doubled = results + [dict(results[0])]
    with pytest.raises(CorpusError, match="duplicate result"):
        score(manifest, doubled)


def test_unequal_review_compute_fails_go_no_go():
    manifest, results = passing_bundle()
    for row in results:
        if row["arm"] == "second_model_review":
            row["compute_cents"] = row["compute_cents"] // 2
    report = score(manifest, results)
    assert "equal_compute_with_review" in _failed(report)
    assert report["go_no_go"]["passed"] is False
