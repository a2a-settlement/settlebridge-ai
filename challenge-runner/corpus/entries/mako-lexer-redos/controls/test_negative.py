"""Negative control: a large benign template parses quickly, so the detector stays silent."""

# Ordinary text and ${} expressions. No unclosed <% tag and no quote run, so
# length alone must not trip the observer.
TEMPLATE = ("hello ${x}\n" * 400) + ("ordinary paragraph text.\n" * 100)


def test_large_benign_template_stays_under_budget(sbc_world):
    outcome = sbc_world.parse(TEMPLATE)
    assert outcome.status == "ok", outcome.error
    assert not outcome.killed
    assert not outcome.exceeded
