import base64
import hashlib
import hmac
import json

import forwarder

SECRET = "whsec_" + base64.b64encode(b"test-secret-key-0123456789").decode()


def _sign(msg_id: str, ts: str, body: bytes) -> str:
    key = base64.b64decode(SECRET.removeprefix("whsec_"))
    mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def _forwarder() -> forwarder.Forwarder:
    return forwarder.Forwarder(
        {
            "RESEND_API_KEY": "re_test",
            "RESEND_WEBHOOK_SECRET": SECRET,
            "FORWARD_TO": "owner@example.com",
            "FORWARD_FROM": "Security <security@a2a-settlement.org>",
            "ACCEPT_ADDRESSES": "security@a2a-settlement.org",
        }
    )


def _event(to: list[str]) -> bytes:
    return json.dumps({"type": "email.received", "data": {"email_id": "em_1", "to": to}}).encode()


def test_signature_accepts_valid_and_rejects_tampered_or_stale():
    body = b'{"a":1}'
    sig = _sign("msg_1", "1000", body)
    assert forwarder.verify_signature(SECRET, "msg_1", "1000", body, sig, now=1000)
    assert forwarder.verify_signature(SECRET, "msg_1", "1000", body, "v1,bogus " + sig, now=1000)
    assert not forwarder.verify_signature(SECRET, "msg_1", "1000", b'{"a":2}', sig, now=1000)
    assert not forwarder.verify_signature(SECRET, "msg_1", "1000", body, sig, now=1000 + 301)
    assert not forwarder.verify_signature(SECRET, "msg_1", "1000", body, "", now=1000)


def test_unsigned_request_is_rejected_without_api_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(forwarder, "_api", lambda *a, **k: calls.append(a))
    status = _forwarder().handle({"svix-id": "m", "svix-timestamp": "1", "svix-signature": "v1,x"}, _event(["security@a2a-settlement.org"]))
    assert status == 401 and calls == []


def test_forwards_security_mail_once_per_event(monkeypatch):
    calls = []

    def api(method, path, key, body=None, idempotency_key=None):
        calls.append((method, path, body, idempotency_key))
        if method == "GET":
            return {"from": "Researcher <r@example.net>", "to": ["security@a2a-settlement.org"], "subject": "bug", "text": "details", "raw": {"download_url": "https://raw.invalid/x"}}
        return {"id": "sent_1"}

    monkeypatch.setattr(forwarder, "_api", api)
    monkeypatch.setattr(forwarder, "_download", lambda url: b"From: r@example.net\r\n\r\ndetails")
    monkeypatch.setattr(forwarder.time, "time", lambda: 2000)
    body = _event(["Security@A2A-Settlement.org"])
    status = _forwarder().handle({"svix-id": "msg_9", "svix-timestamp": "2000", "svix-signature": _sign("msg_9", "2000", body)}, body)
    assert status == 200
    method, path, sent, idem = calls[-1]
    assert (method, path, idem) == ("POST", "/emails", "security-fwd-msg_9")
    assert sent["to"] == ["owner@example.com"]
    assert sent["reply_to"] == "Researcher <r@example.net>"
    assert sent["subject"] == "[security@] bug"
    assert sent["attachments"][0]["filename"] == "original.eml"


def test_other_recipients_are_ignored(monkeypatch):
    calls = []
    monkeypatch.setattr(forwarder, "_api", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(forwarder.time, "time", lambda: 3000)
    body = _event(["sales@a2a-settlement.org"])
    status = _forwarder().handle({"svix-id": "m2", "svix-timestamp": "3000", "svix-signature": _sign("m2", "3000", body)}, body)
    assert status == 204 and calls == []
