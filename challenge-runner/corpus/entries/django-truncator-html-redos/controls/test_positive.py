"""Positive control: a small HTML fragment truncates."""

HTML = "<p>Hello world</p>"


def test_small_html_truncates(sbc_world):
    words = sbc_world.words(HTML, 1)
    chars = sbc_world.chars(HTML, 5)
    assert words.status == "ok", words.error
    assert chars.status == "ok", chars.error
    assert words.value == {"text": "<p>Hello</p>"}
    assert chars.value == {"text": "<p>Hello</p>"}
