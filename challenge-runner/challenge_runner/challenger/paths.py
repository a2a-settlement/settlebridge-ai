"""Normalize challenger test paths. ``..`` is rejected, not resolved away."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from challenge_runner.challenger.limits import MAX_TEST_PATH_CHARS, TEST_PATH_RE

_TEST_PATH = re.compile(TEST_PATH_RE)


def normalize_test_path(raw: object) -> str | None:
    """Return a relative ``challenge_tests/...py`` path, or None when unsafe."""
    if not isinstance(raw, str):
        return None
    if not raw or len(raw) > MAX_TEST_PATH_CHARS:
        return None
    if "\x00" in raw or "\\" in raw or "\n" in raw or "\r" in raw:
        return None
    if raw.startswith("/") or raw.startswith("~"):
        return None
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        return None
    parts: list[str] = []
    for part in pure.parts:
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(part)
    if len(parts) < 2 or parts[0] != "challenge_tests":
        return None
    filename = parts[-1]
    if not filename.endswith(".py") or filename == ".py":
        return None
    normalized = "/".join(parts)
    if not _TEST_PATH.fullmatch(normalized):
        return None
    return normalized
