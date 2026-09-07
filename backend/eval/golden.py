"""Load the frozen golden set and attach code-derived compliance labels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.compliance import check_compliance

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixtures() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        with path.open() as fh:
            row = json.load(fh)
        row["_path"] = str(path)
        rows.append(row)
    return rows


def labeled_fixtures() -> list[dict[str, Any]]:
    """Each fixture plus the live checker result. Labels come from code."""
    out: list[dict[str, Any]] = []
    for row in load_fixtures():
        compliance = check_compliance(row["deliverable"], row["acceptance_criteria"])
        expected = bool(row["label"]["compliant"])
        out.append(
            {
                **row,
                "compliance": compliance,
                "label_compliant": expected,
                "label_matches_checker": compliance["compliant"] == expected,
            }
        )
    return out


def scored_rows(fixtures: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Rows that have an observed judge score, ready for Brier."""
    rows = []
    for item in fixtures if fixtures is not None else labeled_fixtures():
        score = item.get("observed_judge_score")
        if score is None:
            continue
        rows.append(
            {
                "id": item.get("id"),
                "score": float(score),
                "compliant": bool(item["label_compliant"]),
            }
        )
    return rows
