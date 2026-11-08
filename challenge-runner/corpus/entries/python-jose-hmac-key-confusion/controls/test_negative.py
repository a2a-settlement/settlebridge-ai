"""Negative control: a token signed and checked with the shared secret.

The key is not an asymmetric public key, so the world records no protected
write.
"""

from __future__ import annotations


def test_shared_secret_is_authorized(sbc_world):
    token = sbc_world.encode({"sub": "user"})
    outcome = sbc_world.decode(token)
    assert outcome.status == "ok", outcome.error
    assert outcome.accepted, outcome.error
    assert outcome.value["sub"] == "user"
    assert not outcome.unauthorized
