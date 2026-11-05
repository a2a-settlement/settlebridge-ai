"""Mediator verify_patch client. Signature is HMAC over the exact bytes sent."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import types

import httpx
import pytest

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

from app.services import mediator  # noqa: E402


def _install(monkeypatch: pytest.MonkeyPatch, handler, timeouts: list[object]) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(mediator.httpx, "AsyncClient", factory)


def test_verify_patch_signs_exact_body(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mediator.settings, "MEDIATOR_WEBHOOK_SECRET", "topsecret")
    monkeypatch.setattr(mediator.settings, "MEDIATOR_URL", "https://mediator.example")
    payload = {"b": "café", "a": {"d": 1, "c": 2}}
    captured: dict[str, httpx.Request] = {}
    timeouts: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"ok": True, "findings": []})

    _install(monkeypatch, handler, timeouts)
    result = asyncio.run(mediator.verify_patch(payload))
    assert result == {"ok": True, "findings": []}
    request = captured["request"]
    expected_body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    assert request.content == expected_body
    assert expected_body == b'{"a":{"c":2,"d":1},"b":"caf\\u00e9"}'
    digest = hmac.new(b"topsecret", request.content, hashlib.sha256).hexdigest()
    assert request.headers["X-A2ASE-Signature"] == f"sha256={digest}"
    assert request.headers["Content-Type"] == "application/json"
    assert str(request.url) == "https://mediator.example/api/v1/verify-patch"
    assert timeouts == [120]


def test_verify_patch_empty_secret_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mediator.settings, "MEDIATOR_WEBHOOK_SECRET", "")

    def factory(*args, **kwargs):
        raise AssertionError("client should not be constructed")

    monkeypatch.setattr(mediator.httpx, "AsyncClient", factory)
    with pytest.raises(ValueError, match="^MEDIATOR_WEBHOOK_SECRET not configured$"):
        asyncio.run(mediator.verify_patch({"a": 1}))


def test_verify_patch_raises_for_status(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mediator.settings, "MEDIATOR_WEBHOOK_SECRET", "topsecret")
    monkeypatch.setattr(mediator.settings, "MEDIATOR_URL", "https://mediator.example/")
    timeouts: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "down"})

    _install(monkeypatch, handler, timeouts)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(mediator.verify_patch({"z": 1}))
    assert timeouts == [120]
