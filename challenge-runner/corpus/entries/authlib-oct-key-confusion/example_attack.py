"""Stand-in challenger: verify an HMAC token with a public key as the secret."""

from __future__ import annotations


def test_public_key_secret_is_accepted(sbc_world):
    key = "\n" + sbc_world.public_key()
    token = sbc_world.encode({"sub": "user"}, key=key)
    outcome = sbc_world.decode(token, key=key)
    assert outcome.accepted, outcome.error
