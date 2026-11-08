"""Positive control: a digit content-length debits and stays authorized."""

from __future__ import annotations


def test_digit_length_debit_is_authorized(sbc_world):
    sbc_world.feed(b"POST /debit HTTP/1.1\r\n")
    sbc_world.feed(b"Host: ledger.invalid\r\n")
    sbc_world.feed(b"Content-Length: 2\r\n")
    sbc_world.feed(b"\r\n")
    sbc_world.feed(b"40")
    outcome = sbc_world.parse()
    assert outcome.status == "ok", outcome.error
    assert outcome.accepted
    assert not outcome.unauthorized
    assert outcome.value["balance"] == 60
    assert outcome.value["applied"] == [40]
    assert sbc_world.balance() == 60
