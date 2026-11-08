"""Verifier world for starlette-multipart-field-dos (denial_of_service).

The property: Starlette bounds the memory a multipart form field without a
filename can use.

``field`` and ``file`` only describe parts. ``parse`` is the library call: it
sends those parts as one multipart request through ``Request.form`` in a forked
child, then reads the largest non-file field the form actually retained.
File parts are not counted. A part the library rejects is not retained, so it
adds nothing. When the retained size is above ``threshold_mb`` the world
records ``memory_mb``. The default threshold is one mebibyte (1024*1024
bytes). A field of exactly that size does not exceed it.
"""

from __future__ import annotations

import math
import re

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_bytes, require_text

MB = 1024 * 1024
MAX_PARTS = 8
MAX_VALUE_BYTES = 4 * MB
MAX_TOTAL_BYTES = 8 * MB
CHILD_TIMEOUT = 8.0
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_KINDS = frozenset({"field", "file"})


def _require_token(value: object, name: str) -> str:
    text = require_text(value, name, 64)
    if _TOKEN.fullmatch(text) is None:
        raise ValueError(f"{name} must be an ASCII token")
    return text


def _require_threshold(threshold_mb: object) -> float:
    if isinstance(threshold_mb, bool) or not isinstance(threshold_mb, (int, float)):
        raise TypeError("threshold_mb must be a real number")
    threshold = float(threshold_mb)
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("threshold_mb must be a positive finite number")
    return threshold


def _require_parts(parts: object) -> list[dict]:
    if type(parts) is not list:
        raise TypeError("parts must be a plain list")
    if not parts:
        raise ValueError("parts must not be empty")
    if len(parts) > MAX_PARTS:
        raise ValueError(f"parts has more than {MAX_PARTS} items")
    checked: list[dict] = []
    total = 0
    for index, part in enumerate(parts):
        if type(part) is not dict:
            raise TypeError(f"parts[{index}] must be a plain dict")
        kind = part.get("kind")
        if kind not in _KINDS:
            raise ValueError(f"parts[{index}].kind must be 'field' or 'file'")
        name = _require_token(part.get("name"), f"parts[{index}].name")
        value = require_bytes(part.get("value"), f"parts[{index}].value", MAX_VALUE_BYTES)
        total += len(value)
        if total > MAX_TOTAL_BYTES:
            raise ValueError(f"parts are longer than {MAX_TOTAL_BYTES} bytes")
        item = {"kind": kind, "name": name, "value": value}
        if kind == "file":
            item["filename"] = _require_token(part.get("filename"), f"parts[{index}].filename")
        checked.append(item)
    return checked


def _boundary(parts: list[dict]) -> bytes:
    """A boundary token that does not occur in any part value."""
    token = b"sbcbound"
    occupied = [part["value"] for part in parts]
    for count in range(1000):
        if not any(token in value for value in occupied):
            return token
        token = f"sbcbound{count + 1}".encode("ascii")
    raise ValueError("could not choose a multipart boundary")


def _body(parts: list[dict], boundary: bytes) -> bytes:
    chunks: list[bytes] = []
    for part in parts:
        disposition = (
            b'Content-Disposition: form-data; name="' + part["name"].encode("ascii") + b'"'
        )
        if part["kind"] == "file":
            disposition += b'; filename="' + part["filename"].encode("ascii") + b'"'
        chunks.append(b"--" + boundary + b"\r\n" + disposition + b"\r\n\r\n")
        chunks.append(part["value"])
        chunks.append(b"\r\n")
    chunks.append(b"--" + boundary + b"--\r\n")
    return b"".join(chunks)


def _report(
    accepted: bool, field_bytes: int, field_count: int, file_count: int, error: str | None
) -> dict:
    return {
        "accepted": accepted,
        "field_bytes": field_bytes,
        "field_count": field_count,
        "file_count": file_count,
        "error": error,
    }


def _parse(parts: list[dict]) -> dict:
    """Parse one multipart request. Starlette is imported in the child."""
    import asyncio

    from starlette.datastructures import UploadFile
    from starlette.formparsers import MultiPartException
    from starlette.requests import Request

    boundary = _boundary(parts)
    body = _body(parts, boundary)
    content_type = "multipart/form-data; boundary=" + boundary.decode("ascii")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"content-type", content_type.encode("latin-1"))],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 80),
    }

    async def run() -> dict:
        request = Request(scope, receive)
        try:
            form = await request.form()
        except MultiPartException as exc:
            message = getattr(exc, "message", "")
            text = message if isinstance(message, str) and message else str(exc)
            return _report(False, 0, 0, 0, text[:500])
        try:
            field_bytes = 0
            field_count = 0
            file_count = 0
            for _name, value in form.multi_items():
                if isinstance(value, str):
                    field_count += 1
                    size = len(value.encode("utf-8"))
                    if size > field_bytes:
                        field_bytes = size
                elif isinstance(value, UploadFile):
                    file_count += 1
            return _report(True, field_bytes, field_count, file_count, None)
        finally:
            await form.close()

    return asyncio.run(run())


class World(BaseWorld):
    def field(self, name: str, value: bytes) -> dict:
        """Describe one form field with no filename.

        ``name`` is an ASCII token. ``value`` is the field body. Nothing is
        parsed until ``parse``.
        """
        return {
            "kind": "field",
            "name": _require_token(name, "name"),
            "value": require_bytes(value, "value", MAX_VALUE_BYTES),
        }

    def file(self, name: str, filename: str, value: bytes) -> dict:
        """Describe one file part.

        ``name`` and ``filename`` are ASCII tokens. ``value`` is the file body.
        Nothing is parsed until ``parse``.
        """
        return {
            "kind": "file",
            "name": _require_token(name, "name"),
            "filename": _require_token(filename, "filename"),
            "value": require_bytes(value, "value", MAX_VALUE_BYTES),
        }

    def parse(self, parts: list[dict], threshold_mb: float = 1.0):
        """Parse ``parts`` and record ``memory_mb`` when a field exceeds ``threshold_mb``.

        Returns the child Outcome. ``outcome.value`` is the child report:
        ``accepted``, ``field_bytes`` (largest retained non-file field),
        ``field_count``, ``file_count``, and ``error``. ``outcome.exceeded`` is
        true when ``field_bytes`` is above ``threshold_mb`` mebibytes, which is
        also when the resource capture is written. File parts do not add to
        ``field_bytes``. A rejected part is not retained.
        """
        checked = _require_parts(parts)
        threshold = _require_threshold(threshold_mb)
        outcome = self.call(_parse, checked, timeout=CHILD_TIMEOUT)
        outcome.accepted = False
        outcome.exceeded = False
        report = outcome.value
        if outcome.status != "ok" or not isinstance(report, dict):
            return outcome
        raw = report.get("field_bytes")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            return outcome
        outcome.accepted = report.get("accepted") is True
        memory_mb = raw / MB
        outcome.exceeded = memory_mb > threshold
        if outcome.exceeded:
            capture.resource("memory_mb", memory_mb, threshold)
        return outcome
