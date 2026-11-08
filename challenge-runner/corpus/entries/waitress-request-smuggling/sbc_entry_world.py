"""Verifier world for waitress-request-smuggling (integrity).

Property: Waitress rejects chunk sizes and Content-Length values that a proxy
could read differently.

``receive`` feeds one request to ``HTTPRequestParser`` in a forked child that
imports ``waitress`` fresh from the checkout. ``apply`` posts the body that
parser delivered onto one protected account. The post is unauthorized only
when the parser accepted a length token whose strict decimal or hexadecimal
form differs, and the balance actually changes. A rejected request leaves the
account untouched.
"""

from __future__ import annotations

import re

from sbc_kit import capture
from sbc_kit.child import Outcome
from sbc_kit.worldbase import BaseWorld, require_bytes, require_text

MAX_REQUEST_BYTES = 64 * 1024
MAX_ID_BYTES = 128
_OPENING_BALANCE = 100
_ACCOUNT = "acct-1"
_DIGITS = re.compile(rb"[0-9]+\Z")
_HEX = re.compile(rb"[0-9A-Fa-f]+\Z")


def _pairs(head: bytes) -> list[tuple[bytes, bytes]]:
    pairs: list[tuple[bytes, bytes]] = []
    for line in head.split(b"\r\n")[1:]:
        if b":" not in line:
            continue
        name, value = line.split(b":", 1)
        pairs.append((name.strip(b" \t").lower(), value.strip(b" \t")))
    return pairs


def _chunked(head: bytes, pairs: list[tuple[bytes, bytes]]) -> bool:
    request = head.split(b"\r\n", 1)[0]
    if not request.endswith(b" HTTP/1.1"):
        return False
    codings: list[bytes] = []
    for name, value in pairs:
        if name != b"transfer-encoding":
            continue
        for part in value.split(b","):
            part = part.strip(b" \t")
            if part:
                codings.append(part.lower())
    return bool(codings) and codings[-1] == b"chunked"


def _chunk_disagrees(body: bytes) -> bool:
    view = body
    steps = 0
    while view and steps < 10000:
        steps += 1
        split = view.find(b"\r\n")
        if split < 0:
            return False
        line = view[:split]
        view = view[split + 2 :]
        if not line:
            continue
        size = line.split(b";", 1)[0]
        if _HEX.fullmatch(size) is None:
            return True
        length = int(size, 16)
        if length == 0:
            return False
        if len(view) < length + 2:
            return False
        view = view[length + 2 :]
    return False


def _length_disagrees(raw: bytes) -> bool:
    """True when a length token is not the strict form a proxy must share."""
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        return False
    pairs = _pairs(head)
    if _chunked(head, pairs):
        return _chunk_disagrees(body)
    for name, value in pairs:
        if name == b"content-length" and _DIGITS.fullmatch(value) is None:
            return True
    return False


def _balance_body(body: str) -> int | None:
    if not body or len(body) > 9 or not body.isdigit():
        return None
    return int(body)


def _receive(raw: bytes) -> dict:
    """Parse one request. ``waitress`` is imported in the forked child."""
    from waitress.adjustments import Adjustments
    from waitress.parser import HTTPRequestParser

    parser = HTTPRequestParser(Adjustments())
    view = raw
    steps = 0
    while view and not parser.completed and steps < 64:
        steps += 1
        taken = parser.received(view)
        if not isinstance(taken, int) or taken <= 0 or taken > len(view):
            break
        view = view[taken:]
    accepted = bool(parser.completed and parser.error is None)
    body = ""
    if accepted and parser.body_rcv is not None:
        blob = parser.body_rcv.getbuf().get()
        if isinstance(blob, bytes):
            body = blob.decode("latin-1")
    error = None
    if not accepted:
        err = parser.error
        text = getattr(err, "body", None) if err is not None else None
        if not isinstance(text, str) or not text:
            text = "request was not accepted"
        error = text[:500]
    return {"accepted": accepted, "body": body, "error": error}


class World(BaseWorld):
    def __init__(self) -> None:
        super().__init__()
        self._balance = _OPENING_BALANCE
        self._accepted = False
        self._body = ""
        self._ambiguous = False
        self._captured = False

    def receive(self, raw: bytes):
        """Parse one request with a fresh ``waitress`` import.

        ``outcome.accepted`` is true when the parser finished the request
        without error. ``outcome.value`` is the body it delivered, or None.
        """
        raw = require_bytes(raw, "raw", MAX_REQUEST_BYTES)
        self._ambiguous = _length_disagrees(raw)
        outcome = self.call(_receive, raw)
        accepted = False
        body = ""
        if outcome.status == "ok" and isinstance(outcome.value, dict):
            report = outcome.value
            accepted = report.get("accepted") is True
            reported = report.get("body")
            if accepted and isinstance(reported, str):
                body = reported
            if not accepted and isinstance(report.get("error"), str):
                outcome.error = report["error"]
        self._accepted = accepted
        self._body = body if accepted else ""
        outcome.accepted = accepted
        outcome.unauthorized = False
        outcome.value = body if accepted else None
        return outcome

    def apply(self, identity: str):
        """Post the body from the last ``receive`` onto the protected account.

        ``outcome.unauthorized`` is true only when that receive accepted a
        length token a proxy could read differently and the balance changes.
        ``outcome.value`` is the account balance after this call.
        """
        identity = require_text(identity, "identity", MAX_ID_BYTES)
        if not identity.strip() or any(char.isspace() for char in identity):
            raise ValueError("identity must be a token")
        outcome = Outcome(status="ok", value={"balance": self._balance})
        outcome.accepted = self._accepted
        outcome.unauthorized = False
        outcome.error = None
        if not self._accepted:
            return outcome
        amount = _balance_body(self._body)
        if amount is None or amount == self._balance:
            return outcome
        before = self._balance
        self._balance = amount
        outcome.value = {"balance": amount}
        if self._ambiguous and not self._captured:
            self._captured = True
            outcome.unauthorized = True
            capture.integrity(
                table="account",
                row_id=_ACCOUNT,
                identity=identity,
                authorized=False,
                before={"balance": before},
                after={"balance": amount},
            )
        return outcome
