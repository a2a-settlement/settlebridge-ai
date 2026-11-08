"""Stand-in: a length token a proxy could read differently updates the account."""

from __future__ import annotations

_REQUEST = b"POST /acct HTTP/1.1\r\nHost: localhost\r\nContent-Length: +1\r\n\r\n0"


def test_ambiguous_length_updates_account(sbc_world):
    received = sbc_world.receive(_REQUEST)
    posted = sbc_world.apply("attacker")
    assert received.accepted, received.error
    assert posted.unauthorized
    assert posted.value["balance"] == 0
