"""Negative control: a fresh token decoded with its own options dict.

The token is not expired and the dict is not reused, so the world records no
protected write.
"""

from __future__ import annotations

import time


def test_fresh_token_is_authorized(sbc_world):
    token = sbc_world.encode({"sub": "user", "exp": int(time.time()) + 3600})
    outcome = sbc_world.decode(
        token,
        options={"verify_signature": True, "verify_exp": True},
    )
    assert outcome.status == "ok", outcome.error
    assert outcome.accepted, outcome.error
    assert outcome.value["sub"] == "user"
    assert not outcome.unauthorized
