"""Positive control: a shared-secret token round-trips.

The key is an octet secret, so the world records no protected write.
"""

from __future__ import annotations


def test_hmac_round_trip_is_authorized(sbc_world):
    token = sbc_world.encode({"sub": "user"}, algorithm="HS256")
    outcome = sbc_world.decode(token, algorithms=["HS256"])
    assert outcome.status == "ok", outcome.error
    assert outcome.accepted, outcome.error
    assert outcome.value["sub"] == "user"
    assert not outcome.unauthorized
