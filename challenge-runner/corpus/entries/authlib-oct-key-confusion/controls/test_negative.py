"""Negative control: the same token checked with a different symmetric secret.

Verification fails, so the world records no protected write.
"""

from __future__ import annotations


def test_mismatched_secret_is_rejected(sbc_world):
    token = sbc_world.encode({"sub": "user"})
    outcome = sbc_world.decode(token, key="sbc-authlib-other-secret")
    assert outcome.status == "ok", outcome.error
    assert not outcome.accepted
    assert outcome.value is None
    assert not outcome.unauthorized
