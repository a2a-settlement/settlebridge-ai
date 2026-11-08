"""Stand-in challenger: a reused options dict accepts an expired token."""


def test_shared_options_accepts_expired_token(sbc_world):
    outcome = sbc_world.decode_shared_options()
    assert outcome.accepted, outcome.error or outcome.value
