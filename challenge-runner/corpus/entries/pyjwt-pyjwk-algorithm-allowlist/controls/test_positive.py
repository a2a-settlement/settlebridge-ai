"""Positive control: an allow-listed raw key accepts the token it signed.

The key is not a PyJWK, and the token's algorithm is the one the caller
named, so the world records no protected write.
"""

from __future__ import annotations


def test_allowlisted_algorithm_is_accepted(sbc_world):
    token = sbc_world.encode({"sub": "user"})
    outcome = sbc_world.decode(token, algorithms=["HS256"])
    assert outcome.status == "ok", outcome.error
    assert outcome.accepted, outcome.error
    assert outcome.value["sub"] == "user"
    assert not outcome.unauthorized
