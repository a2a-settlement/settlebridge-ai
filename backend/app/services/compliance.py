"""Deterministic deliverable compliance against authored JSON Schema checks.

Code decides whether required structure is present. The LLM judge grades only
quality. Absence of ``custom_checks`` is *not* compliance — ``checked`` is
false so approval gates can distinguish "not authored" from "passed".
"""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

SCHEMA_VERSION = "jsonschema-draft2020-12"

_UNCHECKED: dict[str, Any] = {
    "checked": False,
    "compliant": False,
    "failures": [],
    "schema_version": SCHEMA_VERSION,
}


def is_compliant_for_approval(compliance: dict[str, Any] | None) -> bool:
    """Unchecked criteria do not block approval; a failed check does."""
    if not compliance or not compliance.get("checked"):
        return True
    return bool(compliance.get("compliant"))


def check_compliance(
    deliverable: dict[str, Any] | None,
    criteria: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate ``deliverable`` against ``criteria["custom_checks"]``.

    Each entry in ``custom_checks`` is a JSON Schema (draft 2020-12) document
    applied to the parsed JSON payload of ``deliverable["content"]``.

    Returns ``{"checked", "compliant", "failures", "schema_version"}``.
    Never raises on a non-compliant deliverable.
    """
    checks = _extract_checks(criteria)
    if not checks:
        return dict(_UNCHECKED)

    content = (deliverable or {}).get("content")
    instance, parse_failures = _parse_payload(content, criteria)
    if parse_failures:
        return {
            "checked": True,
            "compliant": False,
            "failures": parse_failures,
            "schema_version": SCHEMA_VERSION,
        }

    failures: list[str] = []
    for i, schema in enumerate(checks):
        if not isinstance(schema, dict):
            failures.append(f"custom_checks[{i}] is not a JSON Schema object")
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            failures.append(f"invalid custom_check schema [{i}]: {exc.message}")
            continue
        validator = Draft202012Validator(schema)
        for err in sorted(validator.iter_errors(instance), key=_error_sort_key):
            failures.append(_format_error(err))

    # Preserve order, drop exact duplicates.
    unique: list[str] = []
    seen: set[str] = set()
    for item in failures:
        if item not in seen:
            seen.add(item)
            unique.append(item)

    return {
        "checked": True,
        "compliant": not unique,
        "failures": unique,
        "schema_version": SCHEMA_VERSION,
    }


def _extract_checks(criteria: dict[str, Any] | None) -> list[Any]:
    if not isinstance(criteria, dict):
        return []
    raw = criteria.get("custom_checks")
    if not raw:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return list(raw)
    return []


def _parse_payload(
    content: Any,
    criteria: dict[str, Any] | None,
) -> tuple[Any, list[str]]:
    if isinstance(content, (dict, list)):
        return content, []
    if not isinstance(content, str) or not content.strip():
        return None, ["deliverable content is empty or not JSON"]
    try:
        return json.loads(content), []
    except (TypeError, ValueError):
        output_format = str((criteria or {}).get("output_format") or "").lower()
        if output_format == "json":
            return None, ["deliverable content is not valid JSON"]
        return None, ["deliverable content is not valid JSON"]


def _error_sort_key(err: Any) -> tuple:
    return (tuple(str(p) for p in err.absolute_path), err.validator or "")


def _json_path(absolute_path: Any) -> str:
    parts: list[str] = []
    for p in absolute_path:
        if isinstance(p, int):
            if not parts:
                parts.append(f"[{p}]")
            else:
                parts[-1] = f"{parts[-1]}[{p}]"
        else:
            parts.append(str(p))
    return ".".join(parts)


def _format_error(err: Any) -> str:
    path = _json_path(err.absolute_path)
    if err.validator == "required":
        missing = _required_property(err)
        if path:
            return f"{path}: missing required key: {missing}"
        return f"missing required key: {missing}"
    if path:
        return f"{path}: {err.message}"
    return str(err.message)


def _required_property(err: Any) -> str:
    message = str(err.message or "")
    if "'" in message:
        return message.split("'")[1]
    validator_value = err.validator_value
    if isinstance(validator_value, list) and validator_value:
        instance_keys = set(err.instance) if isinstance(err.instance, dict) else set()
        for key in validator_value:
            if key not in instance_keys:
                return str(key)
    return message
