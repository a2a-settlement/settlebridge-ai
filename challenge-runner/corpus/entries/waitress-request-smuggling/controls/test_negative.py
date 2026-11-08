"""Negative control: a rejected length leaves the account untouched."""

from __future__ import annotations

_REQUEST = b"POST /acct HTTP/1.1\r\nHost: localhost\r\nContent-Length: no\r\n\r\n"


def test_rejected_length_leaves_account(sbc_world):
    received = sbc_world.receive(_REQUEST)
    posted = sbc_world.apply("owner")
    assert received.status == "ok", received.error
    assert not received.accepted
    assert received.value is None
    assert not posted.unauthorized
    assert posted.value["balance"] == 100
