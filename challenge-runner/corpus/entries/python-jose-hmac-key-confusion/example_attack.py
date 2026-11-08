"""Stand-in challenger: accept an HMAC token whose key is a public key."""

from __future__ import annotations


def test_public_key_verifies_as_hmac(sbc_world):
    key = sbc_world.public_key(encoding="DER")
    token = sbc_world.encode({"sub": "user"}, key=key, algorithm="HS256")
    outcome = sbc_world.decode(token, key=key, algorithms=["HS256"])
    assert outcome.accepted, outcome.error
