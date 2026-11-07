"""Integrity observer: protected row changed by an unauthorized identity."""

from __future__ import annotations

import json
from collections.abc import Iterable

from challenge_runner.observers._io import MAX_EVENTS, clip, load_jsonl
from challenge_runner.protocol import ObserverEvent

_NAME = "integrity"


def _token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 200 or any(char.isspace() for char in text):
        return None
    return text


def _fingerprint(row: dict) -> str | None:
    if "digest" in row:
        digest = row["digest"]
        if isinstance(digest, str) and digest and not any(char.isspace() for char in digest):
            return "d:" + digest
        return None
    if "state" not in row:
        return None
    try:
        encoded = json.dumps(row["state"], sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return "s:" + encoded


class Integrity:
    name = _NAME

    def observe(self, capture_dir: str) -> Iterable[ObserverEvent]:
        try:
            return _observe(capture_dir)
        except Exception:
            return []


def _observe(capture_dir: str) -> list[ObserverEvent]:
    grouped: dict[tuple[str, str], dict] = {}
    for row in load_jsonl(capture_dir, "integrity.jsonl"):
        phase = row.get("phase")
        if phase not in {"before", "after"} or row.get("protected") is not True:
            continue
        table = _token(row.get("table"))
        row_id = _token(row.get("row_id"))
        if table is None or row_id is None:
            continue
        slot = grouped.setdefault((table, row_id), {"before": None, "after": None, "bad": False})
        if slot[phase] is not None:
            slot["bad"] = True
            continue
        slot[phase] = row
    events: list[ObserverEvent] = []
    for (table, row_id), slot in grouped.items():
        if slot["bad"]:
            continue
        before = slot["before"]
        after = slot["after"]
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        if after.get("authorized") is not False:
            continue
        identity = after.get("identity")
        if not isinstance(identity, str) or not identity.strip():
            continue
        before_fp = _fingerprint(before)
        after_fp = _fingerprint(after)
        if before_fp is None or after_fp is None or before_fp == after_fp:
            continue
        events.append(
            ObserverEvent(
                observer=_NAME,
                kind="state_changed",
                detail="protected row changed under an unauthorized identity",
                location=clip(f"{table}/{row_id}"),
            )
        )
        if len(events) >= MAX_EVENTS:
            break
    return events
