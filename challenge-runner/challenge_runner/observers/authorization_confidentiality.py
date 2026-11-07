"""Authorization observer: tenant-B canary visible to tenant-A."""

from __future__ import annotations

from collections.abc import Iterable

from challenge_runner.observers._io import MAX_EVENTS, clip, load_jsonl
from challenge_runner.protocol import ObserverEvent

_NAME = "authorization_confidentiality"
# Short tokens match ordinary tenant-A text. Sixteen is the minimum that fires.
_MIN_TOKEN = 16
_CHANNELS = frozenset({"response", "log", "side_channel"})


def _identity(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().lower().replace("_", "-")
    if text in {"tenant-a", "tenant-b"}:
        return text
    return ""


def _channel(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().lower().replace("-", "_")
    if text in _CHANNELS:
        return text
    return ""


class AuthorizationConfidentiality:
    name = _NAME

    def observe(self, capture_dir: str) -> Iterable[ObserverEvent]:
        try:
            return _observe(capture_dir)
        except Exception:
            return []


def _tokens(rows: list[dict]) -> list[str]:
    tenant_b: set[str] = set()
    tenant_a: set[str] = set()
    for row in rows:
        token = row.get("token")
        owner = _identity(row.get("owner"))
        if not isinstance(token, str) or len(token) < _MIN_TOKEN:
            continue
        if any(char.isspace() for char in token):
            continue
        if owner == "tenant-b":
            tenant_b.add(token)
        elif owner == "tenant-a":
            tenant_a.add(token)
    # A token listed for both tenants is ambiguous and must not fire.
    return sorted(tenant_b - tenant_a)


def _observe(capture_dir: str) -> list[ObserverEvent]:
    tokens = _tokens(load_jsonl(capture_dir, "canaries.jsonl"))
    if not tokens:
        return []
    events: list[ObserverEvent] = []
    for row in load_jsonl(capture_dir, "authorization.jsonl"):
        if _identity(row.get("identity")) != "tenant-a":
            continue
        channel = _channel(row.get("channel"))
        body = row.get("body")
        if not channel or not isinstance(body, str):
            continue
        if not any(token in body for token in tokens):
            continue
        location = row.get("location")
        if not isinstance(location, str) or not location.strip():
            location = channel
        events.append(
            ObserverEvent(
                observer=_NAME,
                kind="canary_seen",
                detail=f"tenant-b canary in tenant-a {channel}",
                location=clip(location),
            )
        )
        if len(events) >= MAX_EVENTS:
            break
    return events
