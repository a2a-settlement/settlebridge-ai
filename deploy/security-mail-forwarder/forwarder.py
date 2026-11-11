"""Forward mail received by Resend for security@a2a-settlement.org to a private inbox.

Resend receives the domain's mail (MX receive.forge.rmta.net) and POSTs an
``email.received`` webhook here. This service checks the Svix signature, fetches
the message from the Received Emails API, and re-sends it to FORWARD_TO from the
security address with the original attached as ``original.eml``. A failed
forward returns 502 so Resend retries; the event id is the idempotency key, so
a retry never sends a second copy.

Environment: RESEND_API_KEY, RESEND_WEBHOOK_SECRET (whsec_...), FORWARD_TO,
FORWARD_FROM, ACCEPT_ADDRESSES (comma separated), LISTEN_PORT.
Standard library only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API = "https://api.resend.com"
PATH = "/hooks/resend-inbound"
MAX_BODY = 1 << 20
MAX_RAW = 30 << 20
TOLERANCE_SECONDS = 300

log = logging.getLogger("security-mail-forwarder")


def verify_signature(secret: str, msg_id: str, timestamp: str, body: bytes, header: str, now: float | None = None) -> bool:
    """Svix scheme: base64 HMAC-SHA256 of ``id.timestamp.body`` keyed by the decoded secret."""
    if not (secret and msg_id and timestamp and header):
        return False
    try:
        sent = int(timestamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - sent) > TOLERANCE_SECONDS:
        return False
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = msg_id.encode() + b"." + timestamp.encode() + b"." + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in header.split():
        version, _, signature = part.partition(",")
        if version == "v1" and hmac.compare_digest(signature, expected):
            return True
    return False


def _api(method: str, path: str, key: str, body: dict | None = None, idempotency_key: str | None = None) -> dict:
    request = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None)
    request.add_header("Authorization", "Bearer " + key)
    request.add_header("User-Agent", "a2a-security-mail-forwarder/1")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if idempotency_key:
        request.add_header("Idempotency-Key", idempotency_key)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _download(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "a2a-security-mail-forwarder/1"}), timeout=60) as response:
        data = response.read(MAX_RAW + 1)
    if len(data) > MAX_RAW:
        raise ValueError("raw message too large")
    return data


def build_forward(email: dict, raw: bytes | None, forward_from: str, forward_to: str) -> dict:
    sender = email.get("from") or "(unknown sender)"
    recipients = ", ".join(email.get("to") or [])
    subject = email.get("subject") or "(no subject)"
    preface = f"Received for {recipients} from {sender}.\nThe original message, with headers and attachments, is attached as original.eml.\n\n"
    message = {
        "from": forward_from,
        "to": [forward_to],
        "subject": f"[security@] {subject}",
        "text": preface + (email.get("text") or "(no plain-text body; see original.eml)"),
    }
    if "@" in sender:
        message["reply_to"] = sender
    if raw is not None:
        message["attachments"] = [{"filename": "original.eml", "content": base64.b64encode(raw).decode()}]
    return message


class Forwarder:
    def __init__(self, env: dict[str, str]):
        self.key = env["RESEND_API_KEY"]
        self.secret = env["RESEND_WEBHOOK_SECRET"]
        self.forward_to = env["FORWARD_TO"]
        self.forward_from = env["FORWARD_FROM"]
        self.accept = {a.strip().lower() for a in env["ACCEPT_ADDRESSES"].split(",") if a.strip()}

    def handle(self, headers, body: bytes) -> int:
        msg_id = headers.get("svix-id", "")
        if not verify_signature(self.secret, msg_id, headers.get("svix-timestamp", ""), body, headers.get("svix-signature", "")):
            return 401
        event = json.loads(body)
        if event.get("type") != "email.received":
            return 204
        data = event.get("data") or {}
        recipients = {addr.lower() for addr in data.get("to") or []}
        if not recipients & self.accept:
            log.info("ignored %s: not addressed to an accepted address", data.get("email_id"))
            return 204
        email_id = data["email_id"]
        email = _api("GET", f"/emails/receiving/{email_id}", self.key)
        raw = None
        raw_url = (email.get("raw") or {}).get("download_url")
        if raw_url:
            try:
                raw = _download(raw_url)
            except (urllib.error.URLError, ValueError) as exc:
                log.warning("forwarding %s without original.eml: %s", email_id, exc)
        message = build_forward(email, raw, self.forward_from, self.forward_to)
        sent = _api("POST", "/emails", self.key, message, idempotency_key=f"security-fwd-{msg_id}")
        log.info("forwarded %s as %s", email_id, sent.get("id"))
        return 200


def make_handler(forwarder: Forwarder):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            if self.path != PATH:
                return self._reply(404)
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._reply(413)
            body = self.rfile.read(length)
            try:
                status = forwarder.handle(self.headers, body)
            except Exception:  # noqa: BLE001 - any failure must make Resend retry
                log.exception("forward failed")
                status = 502
            self._reply(status)

        def _reply(self, status: int):
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return Handler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    forwarder = Forwarder(dict(os.environ))
    port = int(os.environ.get("LISTEN_PORT", "8011"))
    ThreadingHTTPServer(("127.0.0.1", port), make_handler(forwarder)).serve_forever()


if __name__ == "__main__":
    main()
