"""Positive control: a small Content-Type with one parameter parses."""

HEADER = "application/json; charset=utf-8"


def test_small_content_type_parses(sbc_world):
    outcome = sbc_world.parse_content_type(HEADER)
    assert outcome.status == "ok", outcome.error
    assert outcome.value == {
        "content_type": "application/json",
        "options": {"charset": "utf-8"},
    }
