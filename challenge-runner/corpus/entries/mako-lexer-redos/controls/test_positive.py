"""Positive control: a small benign template parses."""

TEMPLATE = "hello ${x}"


def test_small_template_parses(sbc_world):
    outcome = sbc_world.parse(TEMPLATE)
    assert outcome.status == "ok", outcome.error
    assert outcome.value == {"parsed": True, "node": "TemplateNode"}
