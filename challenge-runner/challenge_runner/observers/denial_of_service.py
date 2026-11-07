"""Denial-of-service observer: measured use above the contract threshold."""

from __future__ import annotations

from collections.abc import Iterable
from math import isfinite

from challenge_runner.observers._io import MAX_EVENTS, load_jsonl
from challenge_runner.protocol import ObserverEvent

_NAME = "denial_of_service"
_METRICS = ("cpu_seconds", "wall_seconds", "memory_mb", "disk_mb", "max_processes")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not isfinite(value) or value < 0:
        return None
    return float(value)


class DenialOfService:
    name = _NAME

    def observe(self, capture_dir: str) -> Iterable[ObserverEvent]:
        try:
            return _observe(capture_dir)
        except Exception:
            return []


def _observe(capture_dir: str) -> list[ObserverEvent]:
    events: list[ObserverEvent] = []
    emitted: set[str] = set()
    for row in load_jsonl(capture_dir, "resources.jsonl"):
        metric = row.get("metric")
        if metric not in _METRICS or metric in emitted:
            continue
        value = _number(row.get("value"))
        threshold = _number(row.get("threshold"))
        if value is None or threshold is None or value <= threshold:
            continue
        emitted.add(metric)
        events.append(
            ObserverEvent(
                observer=_NAME,
                kind="threshold_exceeded",
                detail=f"{metric} exceeded the contract threshold",
                location=metric,
            )
        )
        if len(events) >= MAX_EVENTS:
            break
    return events
