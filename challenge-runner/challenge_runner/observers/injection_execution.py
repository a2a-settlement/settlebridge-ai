"""Injection observer: verifier file or command sentinel triggered."""

from __future__ import annotations

from collections.abc import Iterable

from challenge_runner.observers._io import MAX_EVENTS, clip, load_jsonl
from challenge_runner.protocol import ObserverEvent

_NAME = "injection_execution"
_SENTINELS = frozenset({"file", "command"})


class InjectionExecution:
    name = _NAME

    def observe(self, capture_dir: str) -> Iterable[ObserverEvent]:
        try:
            return _observe(capture_dir)
        except Exception:
            return []


def _observe(capture_dir: str) -> list[ObserverEvent]:
    events: list[ObserverEvent] = []
    for row in load_jsonl(capture_dir, "injection.jsonl"):
        if row.get("sentinel") not in _SENTINELS or row.get("triggered") is not True:
            continue
        location = row.get("location")
        if not isinstance(location, str) or not location.strip():
            continue
        events.append(
            ObserverEvent(
                observer=_NAME,
                kind="sentinel_triggered",
                detail="verifier sentinel triggered",
                location=clip(location),
            )
        )
        if len(events) >= MAX_EVENTS:
            break
    return events
