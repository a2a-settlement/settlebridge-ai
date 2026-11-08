"""Stand-in challenger: a length token a proxy would not forward still debits."""

from __future__ import annotations


def test_ambiguous_length_debits_the_account(sbc_world):
    sbc_world.feed(b"POST /debit HTTP/1.1\r\n")
    sbc_world.feed(b"Host: ledger.invalid\r\n")
    sbc_world.feed(b"Content-Length: +2\r\n")
    sbc_world.feed(b"\r\n")
    sbc_world.feed(b"40")
    outcome = sbc_world.parse()
    assert outcome.status == "ok", outcome.error
    assert outcome.accepted
    assert outcome.unauthorized
    assert sbc_world.balance() == 60
