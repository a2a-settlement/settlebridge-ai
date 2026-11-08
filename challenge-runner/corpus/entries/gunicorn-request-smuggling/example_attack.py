"""Stand-in: a transfer carried on bytes the proxy would not forward alone."""

from __future__ import annotations

_TRANSFER = b"POST /transfer HTTP/1.1\r\nHost: localhost\r\nContent-Length: 3\r\n\r\n100"
_FRAMED = b"0\r\n\r\n" + _TRANSFER
_RAW = (
    b"POST /inbox HTTP/1.1\r\n"
    b"Host: localhost\r\n"
    b"Content-Length: " + str(len(_FRAMED)).encode("ascii") + b"\r\n"
    b"Transfer-Encoding: chunked\r\n"
    b"\r\n" + _FRAMED
)


def test_unforwarded_transfer_debits_account(sbc_world):
    sbc_world.feed(_RAW)
    outer = sbc_world.read_request()
    assert outer.accepted, outer.error
    assert outer.value["path"] == "/inbox"
    inner = sbc_world.read_request()
    assert inner.accepted, inner.error
    assert inner.value["path"] == "/transfer"
    assert inner.value["body"] == "100"
    outcome = sbc_world.transfer(inner.value["request_id"])
    assert outcome.applied
    assert outcome.unauthorized
    assert outcome.value["balance"] == 0
    assert not sbc_world.read_request().accepted
