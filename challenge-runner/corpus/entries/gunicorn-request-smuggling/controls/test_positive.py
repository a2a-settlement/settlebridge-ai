"""Positive control: one Content-Length transfer both sides bound the same way.

The debit is applied and the observer stays silent on every commit.
"""

from __future__ import annotations

_RAW = b"POST /transfer HTTP/1.1\r\nHost: localhost\r\nContent-Length: 3\r\n\r\n100"


def test_agreed_length_transfer_is_authorized(sbc_world):
    sbc_world.feed(_RAW)
    message = sbc_world.read_request()
    assert message.status == "ok", message.error
    assert message.accepted, message.error
    assert message.value["path"] == "/transfer"
    assert message.value["body"] == "100"
    outcome = sbc_world.transfer(message.value["request_id"])
    assert outcome.status == "ok", outcome.error
    assert outcome.applied
    assert not outcome.unauthorized
    assert outcome.value["balance"] == 0
    assert not sbc_world.read_request().accepted
