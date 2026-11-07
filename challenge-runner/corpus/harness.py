"""Score challenge arms against the evaluation corpus.

Stdlib only. Structure checks live here; this module does not import jsonschema.
Entries with verified false are not scored unless --allow-unverified. That mode
stamps every report with UNVERIFIED_BANNER and is not a go/no-go result.

Per arm:
- confirmed rate: confirmed verdicts on scored incomplete_fix rows,
  divided by scored incomplete_fix rows
- false confirms: a confirmed row whose category is complete_fix, or whose
  audit is disagree (each confirmed row counts once)
- blind-audit precision: audit agree divided by agree+disagree among confirms
- inconclusive rate, controls-correct rate, accepted tamper count
- median compute cents over confirmed rows (even count: mean of the two middles)
- equal-compute: flag an arm whose total compute_cents differs from
  seeded_challenger by more than 10 percent (exactly 10 percent is not flagged)

Go/no-go applies only to seeded_challenger. Equal-compute is reported and is
not one of those gates. Confirmation counts are all confirmed verdicts on the
arm, not only incomplete_fix rows.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

UNVERIFIED_BANNER = "UNVERIFIED — not a go/no-go result"

SCHEMA_VERSION = 1
CATEGORIES = ("incomplete_fix", "complete_fix", "regression")
FAILURE_CLASSES = (
    "authorization_confidentiality",
    "integrity",
    "injection_execution",
    "denial_of_service",
    "regression",
)
ARMS = ("tests_and_scanners", "second_model_review", "seeded_challenger")
VERDICTS = ("confirmed", "no_failure_found", "inconclusive", "invalid_or_duplicate")
AUDITS = ("agree", "disagree")
CHALLENGER_ARM = "seeded_challenger"
REVIEW_ARM = "second_model_review"

ENTRY_FIELDS = (
    "id",
    "project",
    "ecosystem",
    "advisory_ids",
    "category",
    "fix_commit",
    "base_commit",
    "followup_fix_commit",
    "failure_class",
    "property",
    "oracle_source",
    "notes",
    "held_out",
    "verified",
    "verification",
)
RESULT_FIELDS = (
    "entry_id",
    "arm",
    "verdict",
    "audit",
    "controls_ok",
    "oracle_tamper_accepted",
    "compute_cents",
)
VERIFICATION_FIELDS = ("checked_at", "checked_by", "evidence_urls")

_ID_RE_TEXT = r"[a-z0-9][a-z0-9_-]*"
_PROJECT_RE_TEXT = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
_ADVISORY_RE_TEXT = r"CVE-\d{4}-\d{4,}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}"
_SHA_RE_TEXT = r"[0-9a-f]{40}"


class CorpusError(ValueError):
    """The manifest or a results file does not match the corpus contract."""


class UnverifiedEntriesError(CorpusError):
    """Scoring was refused because a result names an unverified entry."""

    def __init__(self, entry_ids: list[str]) -> None:
        self.entry_ids = list(entry_ids)
        preview = ", ".join(self.entry_ids[:8])
        extra = ""
        if len(self.entry_ids) > 8:
            extra = f" (+{len(self.entry_ids) - 8} more)"
        super().__init__(
            f"Refusing to score {len(self.entry_ids)} unverified entries "
            f"({preview}{extra}). Re-run with --allow-unverified. "
            "That report is not a go/no-go result."
        )


def _fullmatch(pattern: str, value: str) -> bool:
    return re.fullmatch(pattern, value) is not None


def _fail(entry_id: str, message: str) -> None:
    raise CorpusError(f"{entry_id}: {message}")


def _commit(value: Any, field: str, entry_id: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not _fullmatch(_SHA_RE_TEXT, value):
        _fail(entry_id, f"{field} must be null or 40 lowercase hex characters")


def _string_list(value: Any, field: str, entry_id: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        _fail(entry_id, f"{field} must be a list of strings")


def validate_entry(entry: Any, seen_ids: set[str]) -> None:
    """Raise CorpusError when one manifest entry breaks the structure contract."""
    if not isinstance(entry, dict):
        raise CorpusError("entry must be an object")
    entry_id = entry.get("id")
    label = entry_id if isinstance(entry_id, str) else "<missing id>"
    missing = [key for key in ENTRY_FIELDS if key not in entry]
    extra = sorted(set(entry) - set(ENTRY_FIELDS))
    if missing or extra:
        _fail(label, f"missing {missing} extra {extra}")
    if not isinstance(entry_id, str) or not _fullmatch(_ID_RE_TEXT, entry_id):
        _fail(label, "id must match [a-z0-9][a-z0-9_-]*")
    if entry_id in seen_ids:
        _fail(entry_id, "duplicate id")
    seen_ids.add(entry_id)
    project = entry["project"]
    if not isinstance(project, str) or not _fullmatch(_PROJECT_RE_TEXT, project):
        _fail(entry_id, "project must be GitHub owner/name")
    if entry["ecosystem"] != "pypi":
        _fail(entry_id, "ecosystem must be pypi")
    advisories = entry["advisory_ids"]
    _string_list(advisories, "advisory_ids", entry_id)
    for advisory in advisories:
        if not _fullmatch(_ADVISORY_RE_TEXT, advisory):
            _fail(entry_id, f"advisory id {advisory!r} is not a CVE or GHSA")
    if len(set(advisories)) != len(advisories):
        _fail(entry_id, "advisory_ids must be unique")
    if entry["category"] not in CATEGORIES:
        _fail(entry_id, "category is not a known value")
    for field in ("fix_commit", "base_commit", "followup_fix_commit"):
        _commit(entry[field], field, entry_id)
    if entry["category"] != "incomplete_fix" and entry["followup_fix_commit"] is not None:
        _fail(entry_id, "followup_fix_commit is only set for incomplete_fix")
    if entry["failure_class"] not in FAILURE_CLASSES:
        _fail(entry_id, "failure_class is not a known value")
    prop = entry["property"]
    if (
        not isinstance(prop, str)
        or len(prop) < 20
        or "\n" in prop
        or "\r" in prop
        or prop[-1] not in ".?"
    ):
        _fail(entry_id, "property must be one sentence of at least 20 characters")
    if not isinstance(entry["oracle_source"], str) or not entry["oracle_source"]:
        _fail(entry_id, "oracle_source must be a non-empty string")
    if not isinstance(entry["notes"], str) or not entry["notes"]:
        _fail(entry_id, "notes must be a non-empty string")
    if type(entry["held_out"]) is not bool or type(entry["verified"]) is not bool:
        _fail(entry_id, "held_out and verified must be booleans")
    verification = entry["verification"]
    if not isinstance(verification, dict):
        _fail(entry_id, "verification must be an object")
    vmissing = [key for key in VERIFICATION_FIELDS if key not in verification]
    vextra = sorted(set(verification) - set(VERIFICATION_FIELDS))
    if vmissing or vextra:
        _fail(entry_id, f"verification missing {vmissing} extra {vextra}")
    for key in ("checked_at", "checked_by"):
        if verification[key] is not None and not isinstance(verification[key], str):
            _fail(entry_id, f"verification.{key} must be a string or null")
    _string_list(verification["evidence_urls"], "verification.evidence_urls", entry_id)


def validate_manifest(data: Any) -> list[dict[str, Any]]:
    """Return the entry list when the manifest object matches the contract."""
    if not isinstance(data, dict):
        raise CorpusError("manifest must be an object")
    extra = sorted(set(data) - {"schema_version", "entries"})
    missing = [key for key in ("schema_version", "entries") if key not in data]
    if missing or extra:
        raise CorpusError(f"manifest missing {missing} extra {extra}")
    if type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION:
        raise CorpusError("schema_version must be 1")
    entries = data["entries"]
    if not isinstance(entries, list) or not entries:
        raise CorpusError("entries must be a non-empty list")
    seen: set[str] = set()
    for entry in entries:
        validate_entry(entry, seen)
    return entries


def load_manifest(path: Path | str) -> dict[str, Any]:
    """Load a manifest file and reject entries that fail structure checks."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_manifest(document)
    return document


def validate_result(row: Any, line_no: int) -> dict[str, Any]:
    """Raise CorpusError when one results object breaks the results contract."""
    where = f"results line {line_no}"
    if not isinstance(row, dict):
        raise CorpusError(f"{where}: must be an object")
    missing = [key for key in RESULT_FIELDS if key not in row]
    extra = sorted(set(row) - set(RESULT_FIELDS))
    if missing or extra:
        raise CorpusError(f"{where}: missing {missing} extra {extra}")
    if not isinstance(row["entry_id"], str) or not row["entry_id"]:
        raise CorpusError(f"{where}: entry_id must be a non-empty string")
    if row["arm"] not in ARMS:
        raise CorpusError(f"{where}: arm is not a known value")
    if row["verdict"] not in VERDICTS:
        raise CorpusError(f"{where}: verdict is not a known value")
    if row["audit"] is not None and row["audit"] not in AUDITS:
        raise CorpusError(f"{where}: audit must be agree, disagree, or null")
    if type(row["controls_ok"]) is not bool:
        raise CorpusError(f"{where}: controls_ok must be a boolean")
    if type(row["oracle_tamper_accepted"]) is not bool:
        raise CorpusError(f"{where}: oracle_tamper_accepted must be a boolean")
    cents = row["compute_cents"]
    if type(cents) is not int or cents < 0:
        raise CorpusError(f"{where}: compute_cents must be a non-negative integer")
    return row


def load_results(path: Path | str) -> list[dict[str, Any]]:
    """Load a JSONL results file. Blank lines are ignored."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    text = Path(path).read_text(encoding="utf-8")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusError(f"results line {line_no}: {exc}") from exc
        row = validate_result(payload, line_no)
        key = (row["entry_id"], row["arm"])
        if key in seen:
            raise CorpusError(f"results line {line_no}: duplicate result for {key}")
        seen.add(key)
        rows.append(row)
    return rows


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _ge_ratio(numerator: int, denominator: int, threshold_num: int, threshold_den: int) -> bool:
    """True when numerator/denominator >= threshold_num/threshold_den."""
    if denominator <= 0:
        return False
    return numerator * threshold_den >= denominator * threshold_num


def _le_ratio(numerator: int, denominator: int, threshold_num: int, threshold_den: int) -> bool:
    """True when numerator/denominator <= threshold_num/threshold_den."""
    if denominator <= 0:
        return False
    return numerator * threshold_den <= denominator * threshold_num


def median_cents(values: list[int]) -> float:
    """Median. Even counts use the mean of the two middle values."""
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        raise CorpusError("median of an empty sample is undefined")
    mid = count // 2
    if count % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _over_ten_percent(total: int, reference: int) -> bool:
    """True when total differs from reference by more than 10 percent."""
    if reference == 0:
        return total != 0
    return abs(total - reference) * 10 > reference


def _arm_metrics(rows: list[dict[str, Any]], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_category = {category: 0 for category in CATEGORIES}
    for row in rows:
        by_category[by_id[row["entry_id"]]["category"]] += 1
    incomplete = [row for row in rows if by_id[row["entry_id"]]["category"] == "incomplete_fix"]
    confirms = [row for row in rows if row["verdict"] == "confirmed"]
    incomplete_confirmed = sum(1 for row in incomplete if row["verdict"] == "confirmed")
    false_confirms = 0
    for row in confirms:
        category = by_id[row["entry_id"]]["category"]
        if category == "complete_fix" or row["audit"] == "disagree":
            false_confirms += 1
    agrees = sum(1 for row in confirms if row["audit"] == "agree")
    disagrees = sum(1 for row in confirms if row["audit"] == "disagree")
    inconclusive = sum(1 for row in rows if row["verdict"] == "inconclusive")
    controls_ok = sum(1 for row in rows if row["controls_ok"])
    tamper = sum(1 for row in rows if row["oracle_tamper_accepted"])
    costs = [row["compute_cents"] for row in confirms]
    scored = len(rows)
    return {
        "scored": scored,
        "by_category": by_category,
        "incomplete_scored": len(incomplete),
        "incomplete_confirmed": incomplete_confirmed,
        "confirmed_rate_incomplete": _ratio(incomplete_confirmed, len(incomplete)),
        "confirmed": len(confirms),
        "false_confirms": false_confirms,
        "audit_agree": agrees,
        "audit_disagree": disagrees,
        "blind_audit_precision": _ratio(agrees, agrees + disagrees),
        "inconclusive": inconclusive,
        "inconclusive_rate": _ratio(inconclusive, scored),
        "controls_ok": controls_ok,
        "controls_correct_rate": _ratio(controls_ok, scored),
        "accepted_tamper": tamper,
        "median_compute_cents_per_confirmed": median_cents(costs) if costs else None,
        "total_compute_cents": sum(row["compute_cents"] for row in rows),
    }


def _criterion(criterion_id: str, passed: bool, measured: Any, need: str) -> dict[str, Any]:
    return {"id": criterion_id, "passed": passed, "measured": measured, "need": need}


def _absent_go_no_go() -> dict[str, Any]:
    needs = (
        ("incomplete_confirmed_rate", ">= 0.5"),
        ("blind_audit_precision", ">= 0.9"),
        ("false_confirms", "== 0"),
        ("confirmations_ahead_of_second_model", ">= 2"),
        ("inconclusive_rate", "<= 0.2"),
        ("controls_correct_rate", ">= 0.95"),
        ("accepted_tamper", "== 0"),
        ("median_compute_cents", "<= 2500"),
        ("scored_entries", ">= 20"),
        ("scored_incomplete", ">= 6"),
        ("scored_complete", ">= 6"),
        ("scored_regression", ">= 3"),
    )
    criteria = [_criterion(cid, False, None, need) for cid, need in needs]
    return {"arm": CHALLENGER_ARM, "passed": False, "criteria": criteria}


def _go_no_go(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    challenger = arms.get(CHALLENGER_ARM)
    if challenger is None:
        return _absent_go_no_go()
    review = arms.get(REVIEW_ARM)
    if review is None:
        ahead_measured: int | None = None
        ahead_passed = False
    else:
        ahead_measured = challenger["confirmed"] - review["confirmed"]
        ahead_passed = ahead_measured >= 2
    categories = challenger["by_category"]
    median = challenger["median_compute_cents_per_confirmed"]
    criteria = [
        _criterion(
            "incomplete_confirmed_rate",
            _ge_ratio(challenger["incomplete_confirmed"], challenger["incomplete_scored"], 1, 2),
            challenger["confirmed_rate_incomplete"],
            ">= 0.5",
        ),
        _criterion(
            "blind_audit_precision",
            _ge_ratio(
                challenger["audit_agree"],
                challenger["audit_agree"] + challenger["audit_disagree"],
                9,
                10,
            ),
            challenger["blind_audit_precision"],
            ">= 0.9",
        ),
        _criterion(
            "false_confirms",
            challenger["false_confirms"] == 0,
            challenger["false_confirms"],
            "== 0",
        ),
        _criterion("confirmations_ahead_of_second_model", ahead_passed, ahead_measured, ">= 2"),
        _criterion(
            "inconclusive_rate",
            _le_ratio(challenger["inconclusive"], challenger["scored"], 1, 5),
            challenger["inconclusive_rate"],
            "<= 0.2",
        ),
        _criterion(
            "controls_correct_rate",
            _ge_ratio(challenger["controls_ok"], challenger["scored"], 19, 20),
            challenger["controls_correct_rate"],
            ">= 0.95",
        ),
        _criterion(
            "accepted_tamper",
            challenger["accepted_tamper"] == 0,
            challenger["accepted_tamper"],
            "== 0",
        ),
        _criterion(
            "median_compute_cents",
            median is not None and median <= 2500,
            median,
            "<= 2500",
        ),
        _criterion("scored_entries", challenger["scored"] >= 20, challenger["scored"], ">= 20"),
        _criterion(
            "scored_incomplete",
            categories["incomplete_fix"] >= 6,
            categories["incomplete_fix"],
            ">= 6",
        ),
        _criterion(
            "scored_complete",
            categories["complete_fix"] >= 6,
            categories["complete_fix"],
            ">= 6",
        ),
        _criterion(
            "scored_regression",
            categories["regression"] >= 3,
            categories["regression"],
            ">= 3",
        ),
    ]
    return {
        "arm": CHALLENGER_ARM,
        "passed": all(item["passed"] for item in criteria),
        "criteria": criteria,
    }


def _equal_compute(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    reference = arms.get(CHALLENGER_ARM)
    reference_total = None if reference is None else reference["total_compute_cents"]
    details: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        if arm not in arms:
            continue
        total = arms[arm]["total_compute_cents"]
        if reference_total is None:
            rel_diff = None
            flagged = True
        elif reference_total == 0:
            rel_diff = 0.0 if total == 0 else None
            flagged = total != 0
        else:
            rel_diff = round(abs(total - reference_total) / reference_total, 6)
            flagged = _over_ten_percent(total, reference_total)
        if arm == CHALLENGER_ARM:
            flagged = False
        details[arm] = {
            "total_compute_cents": total,
            "rel_diff": rel_diff,
            "flagged": flagged,
        }
    flagged_arms = [arm for arm in ARMS if arm in details and details[arm]["flagged"]]
    return {
        "reference_arm": CHALLENGER_ARM,
        "reference_total_cents": reference_total,
        "flagged": flagged_arms,
        "arms": details,
    }


def score(
    manifest: dict[str, Any],
    results: list[dict[str, Any]],
    *,
    allow_unverified: bool = False,
) -> dict[str, Any]:
    """Score results. Refuse unverified entries unless allow_unverified is set."""
    entries = validate_manifest(manifest)
    by_id = {entry["id"]: entry for entry in entries}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(results, start=1):
        row = validate_result(raw, index)
        key = (row["entry_id"], row["arm"])
        if key in seen:
            raise CorpusError(f"duplicate result for {key}")
        seen.add(key)
        if row["entry_id"] not in by_id:
            raise CorpusError(f"unknown entry_id {row['entry_id']}")
        rows.append(row)
    unverified: list[str] = []
    for row in rows:
        entry_id = row["entry_id"]
        if by_id[entry_id]["verified"] is not True and entry_id not in unverified:
            unverified.append(entry_id)
    if unverified and not allow_unverified:
        raise UnverifiedEntriesError(unverified)
    grouped: dict[str, list[dict[str, Any]]] = {arm: [] for arm in ARMS}
    present: set[str] = set()
    for row in rows:
        grouped[row["arm"]].append(row)
        present.add(row["arm"])
    arms = {arm: _arm_metrics(grouped[arm], by_id) for arm in ARMS if arm in present}
    equal_compute = _equal_compute(arms)
    go_no_go = _go_no_go(arms)
    # "Ahead of the second-model review" is only a fair comparison at equal compute.
    review = equal_compute["arms"].get(REVIEW_ARM)
    go_no_go["criteria"].append(
        _criterion(
            "equal_compute_with_review",
            review is not None and review["flagged"] is False,
            None if review is None else review["rel_diff"],
            "second-model review within 10% of the challenger's total compute",
        )
    )
    go_no_go["passed"] = all(item["passed"] for item in go_no_go["criteria"])
    report = {
        "banner": UNVERIFIED_BANNER if unverified else None,
        "unverified": bool(unverified),
        "arms": arms,
        "equal_compute": equal_compute,
        "go_no_go": go_no_go,
    }
    report["go_no_go"]["authoritative"] = not unverified
    return report


def format_measured(value: Any) -> str:
    """Render a criterion measurement for the text report."""
    if value is None:
        return "null"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.4f}"
    if isinstance(value, float):
        return str(int(value))
    return str(value)


def _rate_cell(value: Any) -> str:
    if value is None:
        return "null"
    return f"{value:.4f}"


def format_text(report: dict[str, Any]) -> str:
    """Short text table, equal-compute flags, and one line per go/no-go criterion."""
    lines: list[str] = []
    if report["banner"]:
        lines.append(report["banner"])
        lines.append("")
    header = (
        f"{'arm':<24}{'inc_rate':>10}{'false':>8}{'precision':>12}"
        f"{'inconcl':>10}{'controls':>10}{'tamper':>8}{'median':>8}{'scored':>8}"
    )
    lines.append(header)
    for arm in ARMS:
        metrics = report["arms"].get(arm)
        if metrics is None:
            continue
        lines.append(
            f"{arm:<24}{_rate_cell(metrics['confirmed_rate_incomplete']):>10}"
            f"{metrics['false_confirms']:>8}{_rate_cell(metrics['blind_audit_precision']):>12}"
            f"{_rate_cell(metrics['inconclusive_rate']):>10}"
            f"{_rate_cell(metrics['controls_correct_rate']):>10}"
            f"{metrics['accepted_tamper']:>8}"
            f"{format_measured(metrics['median_compute_cents_per_confirmed']):>8}"
            f"{metrics['scored']:>8}"
        )
    lines.append("")
    lines.append(f"equal-compute vs {CHALLENGER_ARM}:")
    for arm, info in report["equal_compute"]["arms"].items():
        mark = "FLAG" if info["flagged"] else "ok"
        rel = "null" if info["rel_diff"] is None else f"{info['rel_diff']:.4f}"
        lines.append(f"{mark:<4} {arm} total={info['total_compute_cents']} rel={rel}")
    lines.append("")
    lines.append(f"go/no-go criteria for {CHALLENGER_ARM}:")
    for item in report["go_no_go"]["criteria"]:
        mark = "PASS" if item["passed"] else "FAIL"
        measured = format_measured(item["measured"])
        lines.append(f"{mark} {item['id']} measured={measured}; need {item['need']}")
    passed = "true" if report["go_no_go"]["passed"] else "false"
    authoritative = "true" if report["go_no_go"]["authoritative"] else "false"
    lines.append(f"criteria_all_passed={passed}")
    lines.append(f"authoritative={authoritative}")
    if report["banner"]:
        lines.append(report["banner"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Print the text report and the JSON report. Exit 2 when unverified entries are refused."""
    parser = argparse.ArgumentParser(description="Score challenge corpus results.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="Score entries that are not verified. The report is not a go/no-go result.",
    )
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        results = load_results(args.results)
        report = score(manifest, results, allow_unverified=args.allow_unverified)
    except UnverifiedEntriesError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (CorpusError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sys.stdout.write(format_text(report))
    sys.stdout.write("---\n")
    sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
