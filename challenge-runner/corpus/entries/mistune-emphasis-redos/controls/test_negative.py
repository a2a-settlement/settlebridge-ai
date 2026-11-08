"""Negative control: a large benign document renders quickly, so the detector stays silent."""

# Ordinary paragraphs with closed emphasis and strong markers. No unclosed
# asterisk and no backslash run, so length alone must not trip the observer.
TEXT = "A paragraph with *emphasis* and **strong** words.\n\n" * 400


def test_large_benign_markdown_stays_under_budget(sbc_world):
    outcome = sbc_world.render(TEXT)
    assert outcome.status == "ok", outcome.error
    assert not outcome.killed
    assert not outcome.exceeded
