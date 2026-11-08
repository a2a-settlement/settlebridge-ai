"""Positive control: reusing one options dict accepts an expired token.

The first decode only turns signature verification off. The second turns it
back on and does not set ``verify_exp``. On the vulnerable library the expired
token is accepted, and that acceptance is the unauthorized debit.
"""

from __future__ import annotations

import time


def test_reused_options_accepts_expired_token(sbc_world):
    now = int(time.time())
    fresh = sbc_world.encode({"sub": "user", "exp": now + 3600})
    expired = sbc_world.encode({"sub": "user", "exp": now - 3600})
    first = sbc_world.decode(
        fresh,
        options={"verify_signature": False},
        options_id="shared",
    )
    assert first.accepted, first.error
    assert not first.unauthorized
    outcome = sbc_world.decode(
        expired,
        options={"verify_signature": True},
        options_id="shared",
    )
    assert outcome.accepted, outcome.error
    assert outcome.unauthorized
    assert outcome.value["sub"] == "user"
