"""Negative control: a long plain fragment stays under the CPU budget.

Ordinary words and newlines. No unclosed tag run, so length alone must not
trip the observer.
"""

TEXT = "hello world\n" * 2000


def test_long_plain_text_stays_under_budget(sbc_world):
    outcome = sbc_world.words(TEXT, 10000)
    assert outcome.status == "ok", outcome.error
    assert outcome.value == {"text": TEXT}
    assert not outcome.killed
    assert not outcome.exceeded
