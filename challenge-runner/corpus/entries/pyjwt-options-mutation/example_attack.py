"""Stand-in challenger: reuse one options dict, then accept an expired token."""

from __future__ import annotations

import time


def test_reused_options_accepts_expired_token(sbc_world):
    now = int(time.time())
    fresh = sbc_world.encode({"sub": "user", "exp": now + 3600})
    expired = sbc_world.encode({"sub": "user", "exp": now - 3600})
    sbc_world.decode(fresh, options={"verify_signature": False}, options_id="shared")
    outcome = sbc_world.decode(
        expired,
        options={"verify_signature": True},
        options_id="shared",
    )
    assert outcome.accepted, outcome.error
