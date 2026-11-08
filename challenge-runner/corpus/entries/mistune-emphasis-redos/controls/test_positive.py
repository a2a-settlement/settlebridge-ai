"""Positive control: a small emphasis span renders."""

TEXT = "*hello*"


def test_small_emphasis_renders(sbc_world):
    outcome = sbc_world.render(TEXT)
    assert outcome.status == "ok", outcome.error
    assert outcome.value["rendered"] is True
    assert "<em>hello</em>" in outcome.value["html"]
