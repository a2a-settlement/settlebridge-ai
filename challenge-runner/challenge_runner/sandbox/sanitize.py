"""Scrub runner logs before they are stored. Raw logs are never returned."""

from __future__ import annotations

import json
import re

# 1 MB cap on the artifact the result points at.
MAX_LOG_BYTES = 1024 * 1024

_REDACTED = "[REDACTED]"

_SECRET_PATTERNS = (
    re.compile(r"gh[pos]_[A-Za-z0-9_]{4,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{4,}"),
    re.compile(r"sbl_[A-Za-z0-9_]{4,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        r"-----BEGIN [A-Za-z0-9 ]*PRIVATE KEY-----"
        r"[\s\S]*?"
        r"-----END [A-Za-z0-9 ]*PRIVATE KEY-----"
    ),
    re.compile(r"-----BEGIN [A-Za-z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{8,}={0,2}"),
)


def scrub(text: str, canaries: list[str] | tuple[str, ...] = ()) -> str:
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(_REDACTED, cleaned)
    for canary in sorted({c for c in canaries if c}, key=len, reverse=True):
        cleaned = cleaned.replace(canary, _REDACTED)
    raw = cleaned.encode("utf-8")
    if len(raw) > MAX_LOG_BYTES:
        cleaned = raw[:MAX_LOG_BYTES].decode("utf-8", errors="ignore")
    return cleaned


def parse_canaries(data: bytes) -> list[str]:
    text = data.decode("utf-8-sig", errors="replace")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, str) and item]
        if isinstance(parsed, dict):
            for key in ("canaries", "tokens", "values"):
                values = parsed.get(key)
                if isinstance(values, list):
                    return [item for item in values if isinstance(item, str) and item]
            raise ValueError("canaries format")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
