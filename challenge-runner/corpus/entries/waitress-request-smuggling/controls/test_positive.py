"""Positive control: a digit Content-Length updates the account.

The length token is strict, so the balance change is authorized and the
integrity observer stays silent.
"""

from __future__ import annotations

_REQUEST = b"POST /acct HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1\r\n\r\n7"


def test_digit_length_updates_account(sbc_world):
    received = sbc_world.receive(_REQUEST)
    posted = sbc_world.apply("owner")
    assert received.status == "ok", received.error
    assert received.accepted, received.error
    assert received.value == "7"
    assert not posted.unauthorized
    assert posted.value["balance"] == 7
