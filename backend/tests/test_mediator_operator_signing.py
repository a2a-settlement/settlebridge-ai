"""Signed calls to the mediator's operator routes (/mediate, /audits).

The signature is ``v2=`` + HMAC-SHA256 over
``v2\\n{timestamp}\\n{nonce}\\n{METHOD}\\n{target}\\n{body}``. The known-answer
values below are also asserted in the mediator's own test suite, so the two
repositories cannot drift apart silently.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import sys
import types

import httpx
import pytest

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

from app.services import mediator  # noqa: E402

SECRET = "topsecret"
NONCE = "0123456789abcdefghij"
TRAINING_BODY = b'{"mode":"training","task_type":"recon"}'
# HMAC-SHA256(SECRET, "v2\n1700000000\n" + NONCE + "\nPOST\n/mediate/esc-1\n" + TRAINING_BODY)
KNOWN_MEDIATE = "3b161354e7bf99a50429f28cde82f37f5b831300888654a6b74b26b5dfbb6cef"
# HMAC-SHA256(SECRET, "v2\n1700000000\n" + NONCE + "\nGET\n/audits/esc-1\n")
KNOWN_AUDIT = "a9b40202f1b088408993b4ad610ff1adbf632d23a71be068f72a58f982f4b250"


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Route the client through a mock transport and record what it sends."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"verdict": {"confidence": 0.9, "reasoning": "ok"}})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(mediator.httpx, "AsyncClient", factory)
    monkeypatch.setattr(mediator.settings, "MEDIATOR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(mediator.settings, "MEDIATOR_URL", "https://mediator.example")
    monkeypatch.setattr(mediator, "_timestamp", lambda: 1_700_000_000)
    monkeypatch.setattr(mediator, "_nonce", lambda: NONCE)
    return requests


def _expected(request: httpx.Request, secret: str = SECRET) -> str:
    """Recompute the signature from what actually went over the wire."""
    head = "v2\n{}\n{}\n{}\n".format(
        request.headers["X-A2ASE-Timestamp"],
        request.headers["X-A2ASE-Nonce"],
        request.method,
    ).encode()
    message = head + request.url.raw_path + b"\n" + request.content
    return "v2=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def test_training_mediation_is_signed_with_the_known_answer(sent):
    result = asyncio.run(mediator.trigger_training_mediation("esc-1", "recon"))
    assert result["confidence"] == 0.9
    [request] = sent
    assert request.method == "POST"
    assert str(request.url) == "https://mediator.example/mediate/esc-1"
    assert request.content == TRAINING_BODY
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["X-A2ASE-Timestamp"] == "1700000000"
    assert request.headers["X-A2ASE-Nonce"] == NONCE
    assert request.headers["X-A2ASE-Signature"] == f"v2={KNOWN_MEDIATE}"
    assert request.headers["X-A2ASE-Signature"] == _expected(request)


def test_get_audit_is_signed_with_the_known_answer(sent):
    asyncio.run(mediator.get_audit("esc-1"))
    [request] = sent
    assert request.method == "GET"
    assert str(request.url) == "https://mediator.example/audits/esc-1"
    assert request.content == b""
    assert "Content-Type" not in request.headers
    assert request.headers["X-A2ASE-Signature"] == f"v2={KNOWN_AUDIT}"
    assert request.headers["X-A2ASE-Signature"] == _expected(request)


def test_trigger_mediation_without_options_signs_an_empty_body(sent):
    asyncio.run(mediator.trigger_mediation("esc-1"))
    [request] = sent
    assert request.method == "POST"
    assert request.content == b""
    assert "Content-Type" not in request.headers
    assert request.headers["X-A2ASE-Signature"] == _expected(request)


def test_trigger_mediation_with_options_signs_the_bytes_it_sends(sent):
    asyncio.run(mediator.trigger_mediation("esc-1", mode="production", task_type="café"))
    [request] = sent
    assert request.content == b'{"mode":"production","task_type":"caf\\u00e9"}'
    assert request.headers["X-A2ASE-Signature"] == _expected(request)


def test_signature_is_bound_to_method_and_path(sent):
    asyncio.run(mediator.trigger_mediation("esc-1"))
    asyncio.run(mediator.trigger_mediation("esc-2"))
    asyncio.run(mediator.get_audit("esc-1"))
    signatures = {request.headers["X-A2ASE-Signature"] for request in sent}
    assert len(signatures) == 3
    for request in sent:
        assert request.headers["X-A2ASE-Signature"] == _expected(request)


def test_path_prefix_in_mediator_url_is_not_signed(sent, monkeypatch):
    """A proxy that strips the prefix hands the mediator the bare route."""
    monkeypatch.setattr(mediator.settings, "MEDIATOR_URL", "https://edge.example/mediator/")
    asyncio.run(mediator.get_audit("esc-1"))
    [request] = sent
    assert str(request.url) == "https://edge.example/mediator/audits/esc-1"
    assert request.headers["X-A2ASE-Signature"] == f"v2={KNOWN_AUDIT}"


def test_escrow_id_is_percent_encoded_and_signed_as_sent(sent):
    asyncio.run(mediator.get_audit("esc 1/../x?y=1#z"))
    [request] = sent
    assert request.url.raw_path == b"/audits/esc%201%2F..%2Fx%3Fy%3D1%23z"
    assert request.url.query == b""
    assert request.headers["X-A2ASE-Signature"] == _expected(request)


def test_each_call_uses_a_fresh_nonce_and_timestamp(sent, monkeypatch):
    monkeypatch.undo()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        mediator.httpx,
        "AsyncClient",
        lambda *a, **kw: real_client(*a, **{**kw, "transport": transport}),
    )
    monkeypatch.setattr(mediator.settings, "MEDIATOR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(mediator.settings, "MEDIATOR_URL", "https://mediator.example")
    stamps = iter([1_700_000_000, 1_700_000_007])
    monkeypatch.setattr(mediator, "_timestamp", lambda: next(stamps))

    asyncio.run(mediator.get_audit("esc-1"))
    asyncio.run(mediator.get_audit("esc-1"))
    first, second = requests
    assert first.headers["X-A2ASE-Timestamp"] == "1700000000"
    assert second.headers["X-A2ASE-Timestamp"] == "1700000007"
    assert first.headers["X-A2ASE-Nonce"] != second.headers["X-A2ASE-Nonce"]
    for request in requests:
        # The mediator accepts 16 to 128 characters of this alphabet.
        assert re.fullmatch(r"[A-Za-z0-9_-]{16,128}", request.headers["X-A2ASE-Nonce"])
        assert request.headers["X-A2ASE-Signature"] == _expected(request)
    assert first.headers["X-A2ASE-Signature"] != second.headers["X-A2ASE-Signature"]


def test_identical_calls_in_the_same_second_differ(sent, monkeypatch):
    nonces = iter(["a" * 32, "b" * 32])
    monkeypatch.setattr(mediator, "_nonce", lambda: next(nonces))
    asyncio.run(mediator.get_audit("esc-1"))
    asyncio.run(mediator.get_audit("esc-1"))
    first, second = sent
    assert first.headers["X-A2ASE-Timestamp"] == second.headers["X-A2ASE-Timestamp"]
    assert first.headers["X-A2ASE-Signature"] != second.headers["X-A2ASE-Signature"]


def test_no_secret_sends_the_call_unsigned(sent, monkeypatch):
    """Local development against a mediator that runs without authentication."""
    monkeypatch.setattr(mediator.settings, "MEDIATOR_WEBHOOK_SECRET", "")
    asyncio.run(mediator.trigger_training_mediation("esc-1", "recon"))
    asyncio.run(mediator.get_audit("esc-1"))
    for request in sent:
        assert "X-A2ASE-Signature" not in request.headers
        assert "X-A2ASE-Timestamp" not in request.headers
        assert "X-A2ASE-Nonce" not in request.headers
    assert sent[0].content == TRAINING_BODY


def test_secret_never_appears_in_the_request(sent):
    asyncio.run(mediator.trigger_training_mediation("esc-1", "recon"))
    [request] = sent
    wire = str(request.url) + repr(dict(request.headers)) + request.content.decode()
    assert SECRET not in wire


def test_get_audit_returns_none_on_404(monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"detail": "x"}))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        mediator.httpx,
        "AsyncClient",
        lambda *a, **kw: real_client(*a, **{**kw, "transport": transport}),
    )
    monkeypatch.setattr(mediator.settings, "MEDIATOR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(mediator.settings, "MEDIATOR_URL", "https://mediator.example")
    assert asyncio.run(mediator.get_audit("esc-1")) is None


@pytest.mark.parametrize("status", [401, 409, 503])
def test_auth_failures_raise(monkeypatch, status):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"detail": "x"}))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        mediator.httpx,
        "AsyncClient",
        lambda *a, **kw: real_client(*a, **{**kw, "transport": transport}),
    )
    monkeypatch.setattr(mediator.settings, "MEDIATOR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(mediator.settings, "MEDIATOR_URL", "https://mediator.example")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(mediator.trigger_mediation("esc-1"))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(mediator.get_audit("esc-1"))


def test_verify_patch_keeps_its_own_signature_form(sent):
    asyncio.run(mediator.verify_patch({"a": 1}))
    [request] = sent
    digest = hmac.new(SECRET.encode(), b"1700000000." + request.content, hashlib.sha256).hexdigest()
    assert request.headers["X-A2ASE-Signature"] == f"sha256={digest}"
    assert "X-A2ASE-Nonce" not in request.headers
