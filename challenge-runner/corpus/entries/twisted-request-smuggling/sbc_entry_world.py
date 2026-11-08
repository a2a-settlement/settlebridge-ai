"""Verifier world for twisted-request-smuggling (integrity).

Property: twisted.web rejects request headers and chunk sizes that a proxy
could read differently.

``feed`` appends bytes to one in-memory connection. ``parse`` gives that
buffer to a fresh ``twisted.web.http.HTTPChannel`` in the forked child. The
channel never listens and never schedules a reactor call. Each delivered
``POST /debit`` whose body is a decimal amount from 1 to 100 debits the
account of that amount.

The parent, not the child, decides whether the debit was authorized. It
reads the same bytes the way a strict proxy would: a content-length is one
or more digits, and a chunk size is one or more hex digits with only a
legal chunk extension after ``;``. When Twisted applies a debit that this
reading would not forward, the world records one unauthorized balance change.
A debit both sides would apply, or a message Twisted does not deliver, records
nothing.
"""

from __future__ import annotations

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_bytes

OPENING = 100
MAX_BYTES = 64 * 1024
MAX_MESSAGES = 32
MAX_HEADER_LINES = 64
MAX_CHUNK_LINE = 1024
_HEX = b"0123456789abcdefABCDEF"
_DIGITS = b"0123456789"
_OWS = b" \t"
_TCHAR = frozenset(b"!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


class _Reject(Exception):
    """The buffer is not a strict request stream."""


class _Incomplete(Exception):
    """The buffer ends before the current message is finished."""


def _debit_text(method: str, path: str, body: str) -> int | None:
    """Amount a delivered ``POST /debit`` removes, or ``None`` when it is not one."""
    if method != "POST" or path != "/debit":
        return None
    if not body.isdigit() or len(body) > 3:
        return None
    amount = int(body)
    if amount < 1 or amount > OPENING:
        return None
    return amount


def _ows_strip(value: bytes) -> bytes:
    start = 0
    end = len(value)
    while start < end and value[start] in _OWS:
        start += 1
    while end > start and value[end - 1] in _OWS:
        end -= 1
    return value[start:end]


def _is_token(name: bytes) -> bool:
    return bool(name) and all(byte in _TCHAR for byte in name)


def _chunk_ext_ok(ext: bytes) -> bool:
    """Match the characters the fixed decoder allows after ``;``."""
    for byte in ext:
        if byte == 0x5C or byte == 0x7F:
            return False
        if byte < 0x20 and byte != 0x09:
            return False
        if byte > 0xFF:
            return False
    return True


def _ascii(value: object, limit: int) -> str:
    if not isinstance(value, bytes) or len(value) > limit or not value.isascii():
        return ""
    return value.decode("ascii")


def _parse_chunked(data: bytes, index: int) -> tuple[bytes, int]:
    body = bytearray()
    while True:
        eol = data.find(b"\r\n", index)
        if eol == -1:
            raise _Incomplete()
        line = data[index:eol]
        if len(line) > MAX_CHUNK_LINE:
            raise _Reject()
        semi = line.find(b";")
        size_tok = line if semi == -1 else line[:semi]
        ext = b"" if semi == -1 else line[semi + 1 :]
        if not size_tok or any(byte not in _HEX for byte in size_tok):
            raise _Reject()
        if ext and not _chunk_ext_ok(ext):
            raise _Reject()
        size = int(size_tok, 16)
        index = eol + 2
        if size == 0:
            while True:
                if index + 2 > len(data):
                    raise _Incomplete()
                if data.startswith(b"\r\n", index):
                    return bytes(body), index + 2
                trailer = data.find(b"\r\n", index)
                if trailer == -1:
                    raise _Incomplete()
                index = trailer + 2
        if index + size + 2 > len(data):
            raise _Incomplete()
        if data[index + size : index + size + 2] != b"\r\n":
            raise _Reject()
        body += data[index : index + size]
        index += size + 2


def _headers(data: bytes, index: int) -> tuple[list[tuple[bytes, bytes]], int]:
    fields: list[tuple[bytes, bytes]] = []
    current: tuple[bytes, bytes] | None = None
    lines = 0
    while True:
        if lines > MAX_HEADER_LINES:
            raise _Reject()
        eol = data.find(b"\r\n", index)
        if eol == -1:
            raise _Incomplete()
        line = data[index:eol]
        index = eol + 2
        lines += 1
        if line == b"":
            if current is not None:
                fields.append(current)
            return fields, index
        if line[:1] in _OWS:
            if current is None:
                raise _Reject()
            name, value = current
            current = (name, value + b" " + line.lstrip(_OWS))
            continue
        if current is not None:
            fields.append(current)
        if b":" not in line:
            raise _Reject()
        name, value = line.split(b":", 1)
        if not _is_token(name) or name[-1:] in _OWS:
            raise _Reject()
        current = (name.lower(), _ows_strip(value))


def _one(data: bytes, index: int) -> tuple[int | None, int]:
    eol = data.find(b"\r\n", index)
    if eol == -1:
        raise _Incomplete()
    parts = data[index:eol].split(b" ")
    if len(parts) != 3 or any(part == b"" for part in parts):
        raise _Reject()
    method, target, version = parts
    if version not in (b"HTTP/1.0", b"HTTP/1.1"):
        raise _Reject()
    if not method.isascii() or not target.isascii():
        raise _Reject()
    index = eol + 2
    fields, index = _headers(data, index)
    lengths = [_ows_strip(value) for name, value in fields if name == b"content-length"]
    codings = [_ows_strip(value) for name, value in fields if name == b"transfer-encoding"]
    if lengths and codings:
        raise _Reject()
    if codings:
        if len(codings) != 1 or codings[0].lower() != b"chunked":
            raise _Reject()
        body, index = _parse_chunked(data, index)
    elif lengths:
        if len(lengths) != 1 or not lengths[0] or any(byte not in _DIGITS for byte in lengths[0]):
            raise _Reject()
        size = int(lengths[0])
        if index + size > len(data):
            raise _Incomplete()
        body = data[index : index + size]
        index += size
    else:
        body = b""
    path = target.split(b"?", 1)[0]
    amount = _debit_text(method.decode("ascii"), path.decode("ascii"), _ascii(body, 64))
    return amount, index


def _strict_amounts(raw: bytes) -> list[int] | None:
    """Debits a strict proxy would forward, or ``None`` when it would reject."""
    amounts: list[int] = []
    index = 0
    seen = 0
    try:
        while index < len(raw):
            if seen >= MAX_MESSAGES:
                raise _Reject()
            amount, nxt = _one(raw, index)
            if nxt <= index:
                raise _Reject()
            index = nxt
            seen += 1
            if amount is not None:
                amounts.append(amount)
    except _Reject:
        return None
    except _Incomplete:
        return amounts
    return amounts


def _parse(raw: bytes) -> dict:
    """Deliver requests Twisted's HTTP channel accepts. No listening socket."""
    from twisted.internet.address import IPv4Address
    from twisted.web.http import HTTPChannel, Request

    delivered: list[dict] = []

    class _Transport:
        def __init__(self) -> None:
            self.disconnecting = False

        def write(self, data: bytes) -> None:
            return None

        def writeSequence(self, sequence) -> None:
            for item in sequence:
                self.write(item)

        def loseConnection(self) -> None:
            self.disconnecting = True

        def abortConnection(self) -> None:
            self.disconnecting = True

        def getPeer(self):
            return IPv4Address("TCP", "127.0.0.1", 9)

        def getHost(self):
            return IPv4Address("TCP", "127.0.0.1", 9)

        def pauseProducing(self) -> None:
            return None

        def resumeProducing(self) -> None:
            return None

        def stopProducing(self) -> None:
            return None

        def registerProducer(self, producer, streaming) -> None:
            return None

        def unregisterProducer(self) -> None:
            return None

    class _AppRequest(Request):
        def process(self) -> None:
            content = self.content
            body = b""
            if content is not None:
                content.seek(0)
                body = content.read() or b""
            if not isinstance(body, bytes):
                body = bytes(body)
            delivered.append(
                {
                    "method": _ascii(self.method, 32),
                    "path": _ascii(self.path if isinstance(self.path, bytes) else b"", 256),
                    "body": _ascii(body, 64),
                }
            )
            self.setResponseCode(200)
            self.setHeader(b"Content-Length", b"0")
            self.finish()

    def _factory(channel, queued=0):
        return _AppRequest(channel)

    def _refuse(*_args, **_kwargs):
        raise RuntimeError("in-process verifier does not schedule reactor calls")

    channel = HTTPChannel()
    channel.timeOut = None
    channel.callLater = _refuse
    channel.requestFactory = _factory
    channel.makeConnection(_Transport())
    if raw:
        channel.dataReceived(raw)
    return {"delivered": delivered[:MAX_MESSAGES]}


class World(BaseWorld):
    def __init__(self) -> None:
        super().__init__()
        self._buffer = bytearray()
        self._balance = OPENING
        self._captured = False

    def feed(self, data: bytes) -> None:
        """Append ``data`` to the connection buffer. Does not call the library."""
        block = require_bytes(data, "data", MAX_BYTES)
        if len(self._buffer) + len(block) > MAX_BYTES:
            raise ValueError(f"connection is longer than {MAX_BYTES} bytes")
        self._buffer.extend(block)

    def balance(self) -> int:
        """Return the account balance after the last ``parse``."""
        return self._balance

    def parse(self):
        """Parse the buffer with a fresh Twisted import and apply debits.

        ``outcome.accepted`` is true when at least one debit was applied.
        ``outcome.unauthorized`` is true only when that debit is one a strict
        proxy would not forward, which is also when the balance change is
        recorded. ``outcome.value`` is ``{"balance": int, "applied": list}``.
        """
        raw = bytes(self._buffer)
        outcome = self.call(_parse, raw)
        self._balance = OPENING
        delivered = None
        if outcome.status == "ok" and isinstance(outcome.value, dict):
            delivered = outcome.value.get("delivered")
        applied: list[int] = []
        if isinstance(delivered, list):
            for item in delivered:
                if not isinstance(item, dict):
                    continue
                method = item.get("method")
                path = item.get("path")
                body = item.get("body")
                amount = _debit_text(
                    method if isinstance(method, str) else "",
                    path if isinstance(path, str) else "",
                    body if isinstance(body, str) else "",
                )
                if amount is not None:
                    applied.append(amount)
        else:
            outcome.accepted = False
            outcome.unauthorized = False
            outcome.value = {"balance": self._balance, "applied": []}
            return outcome
        for amount in applied:
            self._balance -= amount
        strict = _strict_amounts(raw)
        unauthorized = bool(applied) and strict != applied
        outcome.accepted = bool(applied)
        outcome.unauthorized = unauthorized
        outcome.value = {"balance": self._balance, "applied": applied}
        if unauthorized and self._balance != OPENING and not self._captured:
            self._captured = True
            capture.integrity(
                table="account",
                row_id="acct-1",
                identity="attacker",
                authorized=False,
                before={"balance": OPENING},
                after={"balance": self._balance},
            )
        return outcome
