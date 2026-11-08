"""Positive control: one decode of a valid, non-expired token."""


def test_valid_token_decodes(sbc_world):
    outcome = sbc_world.decode_fresh(expired=False)
    assert outcome.status == "ok", outcome.error
    assert outcome.value["sub"] == "user"
