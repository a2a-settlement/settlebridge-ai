"""Parse untrusted model output into claim drafts. Invalid claims are counted."""

from __future__ import annotations

import json
import re

from challenge_runner.challenger.limits import (
    FAILURE_CLASSES,
    MAX_ACTION,
    MAX_EXPECTED,
    MAX_IMPACT,
    MAX_MODEL_CHARS,
    MAX_OBSERVED,
    MAX_PRECONDITION,
    MAX_QUOTE,
    MAX_REFERENCE,
    MAX_SETUP,
    MAX_TEST_FILE_BYTES,
    MAX_TEST_FILES_PER_CLAIM,
    MAX_TEST_FILES_TOTAL,
    MAX_TEST_TOTAL_BYTES,
    ORACLE_SOURCES,
)
from challenge_runner.challenger.paths import normalize_test_path

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_CLAIM_KEYS = frozenset(
    {
        "failure_class",
        "scenario",
        "oracle_citation",
        "expected",
        "observed",
        "claimed_impact",
        "test_files",
    }
)
_SCENARIO_KEYS = frozenset({"precondition", "setup", "action", "test_path"})
_CITATION_KEYS = frozenset({"source", "reference", "quote"})
_FILE_KEYS = frozenset({"path", "content"})


def empty_rejection() -> dict:
    return {"count": 0, "reasons": {}}


def merge_rejections(left: dict, right: dict) -> dict:
    reasons: dict[str, int] = {}
    for source in (left, right):
        raw = source.get("reasons") if isinstance(source, dict) else None
        if not isinstance(raw, dict):
            continue
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool):
                reasons[key] = reasons.get(key, 0) + value
    return {"count": sum(reasons.values()), "reasons": reasons}


def parse_model_output(text: object, *, max_claims: int) -> tuple[list[dict], dict]:
    reasons: dict[str, int] = {}
    if not isinstance(text, str):
        _bump(reasons, "not_json")
        return [], _tally(reasons)
    if len(text) > MAX_MODEL_CHARS:
        _bump(reasons, "oversized_output")
        return [], _tally(reasons)
    try:
        data = json.loads(_unwrap(text))
    except json.JSONDecodeError:
        _bump(reasons, "not_json")
        return [], _tally(reasons)
    if not isinstance(data, list):
        _bump(reasons, "not_a_list")
        return [], _tally(reasons)

    accepted: list[dict] = []
    seen: dict[str, bytes] = {}
    total = 0
    for item in data:
        claim, error, files, added = _validate_claim(item, seen_files=seen, total=total)
        if error:
            _bump(reasons, error)
            continue
        if len(accepted) >= max_claims:
            _bump(reasons, "over_max_claims")
            continue
        for path, blob in files:
            seen[path] = blob
        total += added
        accepted.append(claim)
    return accepted, _tally(reasons)


def _unwrap(text: str) -> str:
    stripped = text.strip().lstrip("\ufeff")
    if stripped.startswith("[") or stripped.startswith("{"):
        return stripped
    match = _FENCE.search(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _validate_claim(
    raw: object, *, seen_files: dict[str, bytes], total: int
) -> tuple[dict | None, str | None, list[tuple[str, bytes]], int]:
    if not isinstance(raw, dict):
        return None, "not_an_object", [], 0
    if set(raw) != _CLAIM_KEYS:
        missing = _CLAIM_KEYS - set(raw)
        return None, "missing_field" if missing else "unexpected_field", [], 0
    failure, error = _literal(raw.get("failure_class"), FAILURE_CLASSES, "invalid_failure_class")
    if error:
        return None, error, [], 0
    scenario, error = _scenario(raw.get("scenario"))
    if error:
        return None, error, [], 0
    citation, error = _citation(raw.get("oracle_citation"))
    if error:
        return None, error, [], 0
    expected, error = _text(raw.get("expected"), MAX_EXPECTED)
    if error:
        return None, error, [], 0
    observed, error = _text(raw.get("observed"), MAX_OBSERVED)
    if error:
        return None, error, [], 0
    impact, error = _text(raw.get("claimed_impact"), MAX_IMPACT)
    if error:
        return None, error, [], 0
    files, error = _test_files(raw.get("test_files"))
    if error:
        return None, error, [], 0
    paths = [path for path, _blob in files]
    if scenario["test_path"] not in paths:
        return None, "test_path_mismatch", [], 0
    fresh = [path for path, _blob in files if path not in seen_files]
    if len(seen_files) + len(set(fresh)) > MAX_TEST_FILES_TOTAL:
        return None, "too_many_test_files", [], 0
    added = 0
    counted: set[str] = set()
    for path, blob in files:
        previous = seen_files.get(path)
        if previous is None:
            if path not in counted:
                added += len(blob)
                counted.add(path)
        elif previous != blob:
            return None, "duplicate_test_path", [], 0
    if total + added > MAX_TEST_TOTAL_BYTES:
        return None, "test_total_too_large", [], 0
    claim = {
        "failure_class": failure,
        "scenario": scenario,
        "oracle_citation": citation,
        "expected": expected,
        "observed": observed,
        "claimed_impact": impact,
        "test_files": [{"path": path, "content": blob.decode("utf-8")} for path, blob in files],
    }
    return claim, None, files, added


def _scenario(raw: object) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict):
        return None, "invalid_type"
    if set(raw) != _SCENARIO_KEYS:
        return None, "missing_field" if (_SCENARIO_KEYS - set(raw)) else "unexpected_field"
    cleaned: dict[str, str] = {}
    for key, limit in (
        ("precondition", MAX_PRECONDITION),
        ("setup", MAX_SETUP),
        ("action", MAX_ACTION),
    ):
        value, error = _text(raw.get(key), limit)
        if error:
            return None, error
        cleaned[key] = value
    path = normalize_test_path(raw.get("test_path"))
    if path is None:
        return None, "unsafe_test_path"
    cleaned["test_path"] = path
    return cleaned, None


def _citation(raw: object) -> tuple[dict | None, str | None]:
    if not isinstance(raw, dict):
        return None, "invalid_type"
    if set(raw) != _CITATION_KEYS:
        return None, "missing_field" if (_CITATION_KEYS - set(raw)) else "unexpected_field"
    source, error = _literal(raw.get("source"), ORACLE_SOURCES, "invalid_oracle_source")
    if error:
        return None, error
    reference, error = _text(raw.get("reference"), MAX_REFERENCE)
    if error:
        return None, error
    quote, error = _text(raw.get("quote"), MAX_QUOTE)
    if error:
        return None, error
    return {"source": source, "reference": reference, "quote": quote}, None


def _test_files(raw: object) -> tuple[list[tuple[str, bytes]] | None, str | None]:
    if not isinstance(raw, list):
        return None, "invalid_type"
    if not raw:
        return None, "missing_test_file"
    if len(raw) > MAX_TEST_FILES_PER_CLAIM:
        return None, "too_many_test_files"
    files: list[tuple[str, bytes]] = []
    seen: dict[str, bytes] = {}
    for item in raw:
        if not isinstance(item, dict):
            return None, "invalid_type"
        if set(item) != _FILE_KEYS:
            return None, "missing_field" if (_FILE_KEYS - set(item)) else "unexpected_field"
        path = normalize_test_path(item.get("path"))
        if path is None:
            return None, "unsafe_test_path"
        content = item.get("content")
        if not isinstance(content, str):
            return None, "invalid_type"
        if content == "":
            return None, "field_empty"
        if "\x00" in content:
            return None, "invalid_test_content"
        try:
            blob = content.encode("utf-8")
        except UnicodeError:
            return None, "invalid_test_content"
        if len(blob) > MAX_TEST_FILE_BYTES:
            return None, "test_file_too_large"
        previous = seen.get(path)
        if previous is not None and previous != blob:
            return None, "duplicate_test_path"
        seen[path] = blob
        files.append((path, blob))
    return files, None


def _literal(value: object, allowed: frozenset[str], code: str) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, "invalid_type"
    if value not in allowed:
        return None, code
    return value, None


def _text(value: object, limit: int) -> tuple[str, str] | tuple[None, str]:
    if not isinstance(value, str):
        return None, "invalid_type"
    if value == "":
        return None, "field_empty"
    if len(value) > limit:
        return None, "field_too_long"
    return value, None


def _bump(reasons: dict[str, int], code: str) -> None:
    reasons[code] = reasons.get(code, 0) + 1


def _tally(reasons: dict[str, int]) -> dict:
    return {"count": sum(reasons.values()), "reasons": reasons}
