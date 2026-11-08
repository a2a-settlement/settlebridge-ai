"""Positive control: an HMAC token checked with its symmetric secret.

The key does not parse as a public key or certificate, so the world records
no protected write.
"""

from __future__ import annotations


def test_symmetric_round_trip_is_authorized(sbc_world):
    token = sbc_world.encode({"sub": "user"})
    outcome = sbc_world.decode(token)
    assert outcome.status == "ok", outcome.error
    assert outcome.accepted, outcome.error
    assert outcome.value["sub"] == "user"
    assert not outcome.unauthorized
