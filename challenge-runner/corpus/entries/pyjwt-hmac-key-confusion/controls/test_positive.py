"""Positive control: a symmetric HMAC secret round-trips.

The key is not an asymmetric public key, so a successful verify records
no protected write.
"""

from __future__ import annotations


def test_symmetric_hmac_round_trip(sbc_world):
    token = sbc_world.encode({"sub": "user"})
    outcome = sbc_world.decode(token)
    assert outcome.status == "ok", outcome.error
    assert outcome.accepted, outcome.error
    assert outcome.value["sub"] == "user"
    assert not outcome.unauthorized
