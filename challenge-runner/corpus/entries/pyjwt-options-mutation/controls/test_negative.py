"""Negative control: one decode of an expired token with a fresh options dict.

The library raises ``ExpiredSignatureError``. The world records no modification,
so the observer stays silent.
"""


def test_expired_token_is_rejected(sbc_world):
    outcome = sbc_world.decode_fresh(expired=True)
    assert outcome.status == "error", outcome.value
    assert outcome.error is not None
    assert outcome.error.startswith("ExpiredSignatureError")
