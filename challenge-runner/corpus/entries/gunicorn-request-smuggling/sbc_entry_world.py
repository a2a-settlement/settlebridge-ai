"""Verifier world for gunicorn-request-smuggling (integrity).

Property: Gunicorn rejects a request whose Transfer-Encoding or Content-Length
would let a proxy and Gunicorn disagree about where that request ends.

``feed`` appends bytes to one connection buffer. ``read_request`` parses the
next request with a fresh ``gunicorn`` import and keeps the bytes that parser
did not consume. ``transfer`` applies ``POST /transfer`` from one parsed
request. The body is a decimal debit. The account starts at 100.

A request is forwarded only when a careful proxy can share Gunicorn's framing:
no transfer-coding, a single ``chunked`` coding on HTTP/1.1 with no
Content-Length, or ``identity`` (which does not select a body). Any other
request Gunicorn still accepts, and every later request on that connection,
was not forwarded. A debit on a request that was not forwarded is an
unauthorized change. That debit is the only write the integrity observer sees.
"""

from __future__ import annotations

from sbc_kit import capture
from sbc_kit.child import Outcome
from sbc_kit.worldbase import BaseWorld, require_bytes, require_text

MAX_BYTES = 64 * 1024
MAX_MESSAGES = 8
MAX_ID_BYTES = 64
_START_BALANCE = 100
_ACCOUNT = "acct-1"
_TRANSFER_PATH = "/transfer"
_SAFE_CODINGS = frozenset({"chunked", "identity"})


def _version(value: object) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    major, minor = value
    if isinstance(major, bool) or isinstance(minor, bool):
        return None
    if type(major) is not int or type(minor) is not int:
        return None
    return (major, minor)


def _codings(headers: object) -> tuple[list[str], int]:
    """Transfer-coding values, and how many Content-Length headers were present."""
    codings: list[str] = []
    lengths = 0
    if not isinstance(headers, list):
        return codings, lengths
    for item in headers:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        name, value = item
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        key = name.upper()
        if key == "CONTENT-LENGTH":
            lengths += 1
        elif key == "TRANSFER-ENCODING":
            codings.append(value.lower())
    return codings, lengths


def _framing_disagrees(version: object, headers: object) -> bool:
    """True when a proxy and Gunicorn can bound this accepted request differently.

    ``identity`` and one exact ``chunked`` coding on HTTP/1.1, with no
    Content-Length beside ``chunked``, name a single body. A second length, a
    second coding, ``chunked`` before HTTP/1.1, or any other coding value does
    not.
    """
    codings, lengths = _codings(headers)
    if lengths > 1:
        return True
    if any(coding not in _SAFE_CODINGS for coding in codings):
        return True
    chunked = [coding for coding in codings if coding == "chunked"]
    if not chunked:
        return False
    if len(chunked) > 1 or len(codings) != 1 or lengths:
        return True
    parsed = _version(version)
    return parsed is None or parsed < (1, 1)


def _amount(body: object) -> int | None:
    if type(body) is not str or not body.isdigit() or body[0] == "0" or len(body) > 9:
        return None
    value = int(body)
    if value <= 0 or value > 1_000_000:
        return None
    return value


def _parse_one(data: bytes) -> dict:
    """Parse one request. ``gunicorn`` is imported in the forked child."""
    from gunicorn.config import Config
    from gunicorn.http.parser import RequestParser

    try:
        parser = RequestParser(Config(), [data], ("127.0.0.1", 80))
        request = next(parser)
        body = request.body.read()
        unread = request.unreader.buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        text = f"{type(exc).__name__}: {exc}"
        return {"accepted": False, "error": text[:500]}
    if not isinstance(body, bytes) or not isinstance(unread, bytes):
        return {"accepted": False, "error": "parser did not return bytes"}
    if not isinstance(request.method, str) or not isinstance(request.path, str):
        return {"accepted": False, "error": "parser did not return a request target"}
    headers: list[list[str]] = []
    for name, value in request.headers:
        if isinstance(name, str) and isinstance(value, str):
            headers.append([name, value])
    version = list(request.version) if isinstance(request.version, tuple) else None
    return {
        "accepted": True,
        "method": request.method,
        "path": request.path,
        "version": version,
        "headers": headers,
        "body": body.decode("latin-1"),
        "unread": unread.decode("latin-1"),
        "consumed": len(data) - len(unread),
        "error": None,
    }


class World(BaseWorld):
    def __init__(self) -> None:
        super().__init__()
        self._buf = b""
        self._messages: dict[str, dict] = {}
        self._seq = 0
        self._desynced = False
        self._balance = _START_BALANCE
        self._captured = False

    def feed(self, data: bytes) -> int:
        """Append ``data`` to the connection buffer.

        Returns the buffered length. This does not call gunicorn.
        """
        block = require_bytes(data, "data", MAX_BYTES)
        if len(self._buf) + len(block) > MAX_BYTES:
            raise ValueError(f"connection is longer than {MAX_BYTES} bytes")
        self._buf += block
        return len(self._buf)

    def _idle(self, error: str) -> Outcome:
        outcome = Outcome(status="ok", error=error)
        outcome.accepted = False
        outcome.value = None
        return outcome

    def read_request(self):
        """Parse one request from the unread buffer with a fresh gunicorn import.

        ``outcome.accepted`` is true when gunicorn returned that request and
        its body. ``outcome.value`` then holds ``request_id``, ``method``,
        ``path``, and ``body``. A request gunicorn rejects leaves the buffer
        where it was.
        """
        if not self._buf:
            return self._idle("no bytes buffered")
        if len(self._messages) >= MAX_MESSAGES:
            raise ValueError(f"more than {MAX_MESSAGES} requests")
        raw = self._buf
        outcome = self.call(_parse_one, raw)
        report = outcome.value
        if (
            outcome.status != "ok"
            or not isinstance(report, dict)
            or report.get("accepted") is not True
        ):
            error = report.get("error") if isinstance(report, dict) else None
            if isinstance(error, str) and error:
                outcome.error = error
            outcome.accepted = False
            outcome.value = None
            return outcome
        consumed = report.get("consumed")
        unread = report.get("unread")
        method = report.get("method")
        path = report.get("path")
        body = report.get("body")
        remainder_ok = (
            type(consumed) is int
            and 0 < consumed <= len(raw)
            and type(unread) is str
            and raw[consumed:].decode("latin-1") == unread
            and type(method) is str
            and type(path) is str
            and type(body) is str
        )
        if not remainder_ok:
            outcome.accepted = False
            outcome.error = "parser remainder did not match"
            outcome.value = None
            return outcome
        disagrees = _framing_disagrees(report.get("version"), report.get("headers"))
        forwarded = not disagrees and not self._desynced
        if disagrees:
            self._desynced = True
        self._buf = raw[consumed:]
        self._seq += 1
        request_id = f"m{self._seq}"
        self._messages[request_id] = {
            "method": method,
            "path": path,
            "body": body,
            "forwarded": forwarded,
            "used": False,
        }
        outcome.accepted = True
        outcome.error = None
        outcome.value = {
            "request_id": request_id,
            "method": method,
            "path": path,
            "body": body,
        }
        return outcome

    def _unchanged(self) -> Outcome:
        outcome = Outcome(status="ok")
        outcome.applied = False
        outcome.unauthorized = False
        outcome.value = {"balance": self._balance, "applied": False}
        return outcome

    def transfer(self, request_id: str):
        """Apply ``POST /transfer`` for ``request_id``.

        The body is a decimal debit of an account that starts at 100. A debit
        the proxy would have forwarded changes the balance and stays silent.
        A debit taken from a request the proxy would not have forwarded changes
        the balance and records one unauthorized integrity write. Any other id
        leaves the balance where it is.
        """
        request_id = require_text(request_id, "request_id", MAX_ID_BYTES)
        message = self._messages.get(request_id)
        amount = None if message is None else _amount(message.get("body"))
        if (
            message is None
            or message["used"]
            or message["method"] != "POST"
            or message["path"] != _TRANSFER_PATH
            or amount is None
            or amount > self._balance
        ):
            return self._unchanged()
        message["used"] = True
        before = self._balance
        self._balance = before - amount
        unauthorized = not message["forwarded"]
        if unauthorized and not self._captured and before != self._balance:
            self._captured = True
            capture.integrity(
                table="account",
                row_id=_ACCOUNT,
                identity="attacker",
                authorized=False,
                before={"balance": before},
                after={"balance": self._balance},
            )
        outcome = Outcome(status="ok")
        outcome.applied = True
        outcome.unauthorized = unauthorized
        outcome.value = {"balance": self._balance, "applied": True}
        return outcome
