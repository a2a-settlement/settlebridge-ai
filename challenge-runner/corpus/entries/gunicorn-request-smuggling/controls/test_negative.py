"""Negative control: one exact chunked request that does not transfer.

Both revisions accept the framing. The account does not move, so the observer
stays silent.
"""

from __future__ import annotations

_RAW = b"POST /health HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"


def test_chunked_health_check_does_not_move_funds(sbc_world):
    sbc_world.feed(_RAW)
    message = sbc_world.read_request()
    assert message.status == "ok", message.error
    assert message.accepted, message.error
    assert message.value["path"] == "/health"
    assert message.value["body"] == ""
    outcome = sbc_world.transfer(message.value["request_id"])
    assert outcome.status == "ok", outcome.error
    assert not outcome.applied
    assert not outcome.unauthorized
    assert outcome.value["balance"] == 100
    assert not sbc_world.read_request().accepted
