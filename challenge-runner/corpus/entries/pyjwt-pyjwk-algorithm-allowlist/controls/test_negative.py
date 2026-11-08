"""Negative control: an algorithm outside the allow-list is rejected.

The token is checked with a raw key, and the world records no protected write.
"""

from __future__ import annotations


def test_algorithm_outside_allowlist_is_rejected(sbc_world):
    token = sbc_world.encode({"sub": "user"})
    outcome = sbc_world.decode(token, algorithms=["HS384"])
    assert outcome.status == "ok", outcome.error
    assert not outcome.accepted
    assert not outcome.unauthorized
