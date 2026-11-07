"""Regression observer: one trusted fixture test failed in this run.

Pass-on-base and fail-on-target is decided by comparing this per-run event
across the base and target results, not inside a single capture.
"""

from __future__ import annotations

from collections.abc import Iterable

from challenge_runner.observers._io import MAX_EVENTS, load_jsonl
from challenge_runner.protocol import ObserverEvent

_NAME = "regression"


def _test_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > 200:
        return None
    if text.startswith("challenge_tests/") or text.startswith("challenge_tests\\"):
        return None
    if ".." in text.split("/"):
        return None
    return text


class Regression:
    name = _NAME

    def observe(self, capture_dir: str) -> Iterable[ObserverEvent]:
        try:
            return _observe(capture_dir)
        except Exception:
            return []


def _observe(capture_dir: str) -> list[ObserverEvent]:
    events: list[ObserverEvent] = []
    for row in load_jsonl(capture_dir, "regression.jsonl"):
        if row.get("trusted") is not True or row.get("outcome") != "failed":
            continue
        test_id = _test_id(row.get("test_id"))
        if test_id is None:
            continue
        events.append(
            ObserverEvent(
                observer=_NAME,
                kind="state_changed",
                detail="trusted acceptance test failed",
                location=test_id,
            )
        )
        if len(events) >= MAX_EVENTS:
            break
    return events
