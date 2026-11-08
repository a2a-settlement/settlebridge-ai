"""Positive control: a small benign package page parses."""

PAGE = '<a href="https://example.com/home" rel="homepage">Home</a>'


def test_small_page_parses(sbc_world):
    outcome = sbc_world.parse_page(PAGE)
    assert outcome.status == "ok", outcome.error
    assert outcome.value == {
        "parsed": True,
        "links": ["https://example.com/home"],
    }
