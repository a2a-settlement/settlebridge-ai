"""Negative control: escaped values and ordinary mixed-case attributes stay put."""


def test_semicolon_in_the_value_stays_in_the_value(sbc_world):
    outcome = sbc_world.set_cookie("sid", "a;b")
    assert outcome.status == "ok", outcome.error
    assert outcome.injected is False
    assert outcome.value["header"] == 'sid="a\\073b"; Path=/'


def test_mixed_case_attributes_match_what_was_declared(sbc_world):
    outcome = sbc_world.set_cookie("sid", "ok", {"HttpOnly": True, "pATH": "/foo"})
    assert outcome.status == "ok", outcome.error
    assert outcome.injected is False
    assert outcome.value["header"] == "sid=ok; HttpOnly; Path=/foo"
