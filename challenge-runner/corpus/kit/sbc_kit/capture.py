"""Append-only capture writer for the frozen observers (JSON Lines, bounded)."""

from __future__ import annotations

import json
import os
from pathlib import Path

CAPTURE_DIR = Path(
    os.environ.get(
        "SBC_CAPTURE_DIR",
        str(Path(os.environ.get("SBC_VERIFIER_ROOT", "/verifier")) / "capture"),
    )
)
MAX_RECORDS_PER_FILE = 200

_counts: dict[str, int] = {}


def _append(name: str, record: dict) -> None:
    if _counts.get(name, 0) >= MAX_RECORDS_PER_FILE:
        return
    _counts[name] = _counts.get(name, 0) + 1
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n"
    with open(CAPTURE_DIR / name, "a", encoding="utf-8") as handle:
        handle.write(line)


def injection(sentinel: str, location: str) -> None:
    _append("injection.jsonl", {"sentinel": sentinel, "triggered": True, "location": location})


def resource(metric: str, value: float, threshold: float) -> None:
    if value > threshold:
        _append("resources.jsonl", {"metric": metric, "value": value, "threshold": threshold})


def integrity(*, table: str, row_id: str, identity: str, authorized: bool, before, after) -> None:
    base = {"identity": identity, "table": table, "row_id": row_id, "protected": True}
    _append("integrity.jsonl", {**base, "phase": "before", "authorized": True, "state": before})
    _append("integrity.jsonl", {**base, "phase": "after", "authorized": authorized, "state": after})


def canary(token: str, owner: str) -> None:
    _append("canaries.jsonl", {"token": token, "owner": owner})


def authorization(identity: str, channel: str, body: str, location: str) -> None:
    _append(
        "authorization.jsonl",
        {"identity": identity, "channel": channel, "body": body[:60000], "location": location},
    )
