"""Per-purpose mediator signing secrets.

Each kind of call is signed with its own secret when one is configured and
with ``MEDIATOR_WEBHOOK_SECRET`` otherwise. The signature formats do not
change, so the expected values are recomputed from what went over the wire.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import sys
import types

import httpx
import pytest

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

from app.config import Settings  # noqa: E402
from app.services import mediator  # noqa: E402

SHARED = "shared-webhook-secret"
OWN = {
    "operator": "operator-only-secret",
    "verify-patch": "verify-patch-only-secret",
    "receipts": "receipts-only-secret",
}
SETTING = {
    "operator": "MEDIATOR_OPERATOR_SECRET",
    "verify-patch": "MEDIATOR_VERIFY_PATCH_SECRET",
    "receipts": "MEDIATOR_RECEIPTS_SECRET",
}
purposes = pytest.mark.parametrize("purpose", list(SETTING))


def _configure(monkeypatch: pytest.MonkeyPatch, shared: str = "", **own: str) -> None:
    monkeypatch.setattr(mediator.settings, "MEDIATOR_WEBHOOK_SECRET", shared)
    for purpose, name in SETTING.items():
        monkeypatch.setattr(mediator.settings, name, own.get(purpose.replace("-", "_"), ""))


def _all_own() -> dict[str, str]:
    return {purpose.replace("-", "_"): secret for purpose, secret in OWN.items()}


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        mediator.httpx,
        "AsyncClient",
        lambda *a, **kw: real_client(*a, **{**kw, "transport": transport}),
    )
    monkeypatch.setattr(mediator.settings, "MEDIATOR_URL", "https://mediator.example")
    monkeypatch.setattr(mediator, "_timestamp", lambda: 1_700_000_000)
    return requests


def _operator_signature(request: httpx.Request, secret: str) -> str:
    head = "v2\n{}\n{}\n{}\n".format(
        request.headers["X-A2ASE-Timestamp"],
        request.headers["X-A2ASE-Nonce"],
        request.method,
    ).encode()
    message = head + request.url.raw_path + b"\n" + request.content
    return "v2=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _verify_patch_signature(request: httpx.Request, secret: str) -> str:
    message = request.headers["X-A2ASE-Timestamp"].encode() + b"." + request.content
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _operator_calls() -> None:
    asyncio.run(mediator.trigger_mediation("esc-1", mode="production"))
    asyncio.run(mediator.trigger_training_mediation("esc-1", "recon"))
    asyncio.run(mediator.get_audit("esc-1"))


def test_new_settings_default_to_empty():
    for name in SETTING.values():
        assert Settings.model_fields[name].default == ""


@purposes
def test_signing_secret_falls_back_to_the_webhook_secret(monkeypatch, purpose):
    _configure(monkeypatch, SHARED)
    assert mediator.signing_secret(purpose) == SHARED


@purposes
def test_signing_secret_prefers_the_purpose_secret(monkeypatch, purpose):
    _configure(monkeypatch, SHARED, **_all_own())
    assert mediator.signing_secret(purpose) == OWN[purpose]
    _configure(monkeypatch, "", **_all_own())
    assert mediator.signing_secret(purpose) == OWN[purpose]


@purposes
def test_one_purpose_secret_leaves_the_others_on_the_webhook_secret(monkeypatch, purpose):
    _configure(monkeypatch, SHARED, **{purpose.replace("-", "_"): OWN[purpose]})
    for other in SETTING:
        expected = OWN[purpose] if other == purpose else SHARED
        assert mediator.signing_secret(other) == expected


@purposes
def test_signing_secret_is_empty_when_nothing_is_set(monkeypatch, purpose):
    _configure(monkeypatch)
    assert mediator.signing_secret(purpose) == ""


def test_unknown_purpose_is_an_error(monkeypatch):
    _configure(monkeypatch, SHARED)
    with pytest.raises(KeyError):
        mediator.signing_secret("webhook")


def test_operator_calls_use_the_operator_secret(sent, monkeypatch):
    _configure(monkeypatch, SHARED, **_all_own())
    _operator_calls()
    assert len(sent) == 3
    for request in sent:
        signature = request.headers["X-A2ASE-Signature"]
        assert signature == _operator_signature(request, OWN["operator"])
        for wrong in (SHARED, OWN["verify-patch"], OWN["receipts"]):
            assert signature != _operator_signature(request, wrong)


def test_operator_calls_fall_back_to_the_webhook_secret(sent, monkeypatch):
    # The other purposes having their own secret must not affect this one.
    _configure(monkeypatch, SHARED, verify_patch=OWN["verify-patch"], receipts=OWN["receipts"])
    _operator_calls()
    assert len(sent) == 3
    for request in sent:
        assert request.headers["X-A2ASE-Signature"] == _operator_signature(request, SHARED)


def test_operator_calls_are_signed_without_a_webhook_secret(sent, monkeypatch):
    _configure(monkeypatch, "", operator=OWN["operator"])
    _operator_calls()
    for request in sent:
        assert request.headers["X-A2ASE-Signature"] == _operator_signature(
            request, OWN["operator"]
        )


def test_operator_calls_stay_unsigned_when_only_other_purposes_have_secrets(sent, monkeypatch):
    _configure(monkeypatch, "", verify_patch=OWN["verify-patch"], receipts=OWN["receipts"])
    _operator_calls()
    assert len(sent) == 3
    for request in sent:
        assert "X-A2ASE-Signature" not in request.headers
        assert "X-A2ASE-Nonce" not in request.headers


def test_verify_patch_uses_the_verify_patch_secret(sent, monkeypatch):
    _configure(monkeypatch, SHARED, **_all_own())
    asyncio.run(mediator.verify_patch({"a": 1}))
    [request] = sent
    signature = request.headers["X-A2ASE-Signature"]
    assert signature == _verify_patch_signature(request, OWN["verify-patch"])
    for wrong in (SHARED, OWN["operator"], OWN["receipts"]):
        assert signature != _verify_patch_signature(request, wrong)
    assert "X-A2ASE-Nonce" not in request.headers


def test_verify_patch_falls_back_to_the_webhook_secret(sent, monkeypatch):
    _configure(monkeypatch, SHARED, operator=OWN["operator"], receipts=OWN["receipts"])
    asyncio.run(mediator.verify_patch({"a": 1}))
    [request] = sent
    assert request.headers["X-A2ASE-Signature"] == _verify_patch_signature(request, SHARED)


def test_verify_patch_works_without_a_webhook_secret(sent, monkeypatch):
    _configure(monkeypatch, "", verify_patch=OWN["verify-patch"])
    asyncio.run(mediator.verify_patch({"a": 1}))
    [request] = sent
    assert request.headers["X-A2ASE-Signature"] == _verify_patch_signature(
        request, OWN["verify-patch"]
    )


def test_verify_patch_still_refuses_when_only_other_purposes_have_secrets(sent, monkeypatch):
    _configure(monkeypatch, "", operator=OWN["operator"], receipts=OWN["receipts"])
    with pytest.raises(ValueError, match="^MEDIATOR_WEBHOOK_SECRET not configured$"):
        asyncio.run(mediator.verify_patch({"a": 1}))
    assert sent == []


def test_no_secret_reaches_the_wire(sent, monkeypatch):
    _configure(monkeypatch, SHARED, **_all_own())
    _operator_calls()
    asyncio.run(mediator.verify_patch({"a": 1}))
    for request in sent:
        wire = str(request.url) + repr(dict(request.headers)) + request.content.decode()
        for secret in (SHARED, *OWN.values()):
            assert secret not in wire
