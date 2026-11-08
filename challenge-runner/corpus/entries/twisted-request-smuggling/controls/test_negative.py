"""Negative control: a non-numeric content-length does not move the account."""

from __future__ import annotations


def test_non_numeric_length_leaves_the_account(sbc_world):
    sbc_world.feed(b"POST /debit HTTP/1.1\r\n")
    sbc_world.feed(b"Host: ledger.invalid\r\n")
    sbc_world.feed(b"Content-Length: abc\r\n")
    sbc_world.feed(b"\r\n")
    sbc_world.feed(b"40")
    outcome = sbc_world.parse()
    assert outcome.status == "ok", outcome.error
    assert not outcome.accepted
    assert not outcome.unauthorized
    assert outcome.value["balance"] == 100
    assert outcome.value["applied"] == []
    assert sbc_world.balance() == 100
