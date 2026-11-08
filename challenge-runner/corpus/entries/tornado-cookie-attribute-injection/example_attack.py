"""Stand-in for the challenger: attribute text must not become another attribute."""


def test_attribute_text_adds_a_cookie_attribute(sbc_world):
    outcome = sbc_world.set_cookie("sid", "ok", {"Path": "/admin; Secure"})
    assert outcome.injected, (outcome.status, outcome.error)
