"""Stand-in for the challenger: a slash in the key breaks out of the attribute."""


def test_slash_in_key_breaks_out(sbc_world):
    outcome = sbc_world.render({"x/onmouseover": "1"})
    assert outcome.broke_out, (outcome.status, outcome.error, outcome.value)
