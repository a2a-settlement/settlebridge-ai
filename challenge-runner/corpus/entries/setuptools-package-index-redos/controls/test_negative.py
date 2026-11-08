"""Negative control: a large benign page parses quickly, so the detector stays silent."""

# Closed tags, ordinary text, and whitespace inside a finished element. No
# unclosed rel attribute, so length alone must not trip the observer.
PARAGRAPHS = "<p>ordinary paragraph text.</p>\n" * 500
LINKS = "".join(
    f'<a href="https://example.com/p/{i}" rel="homepage">item {i}</a>\n' for i in range(40)
)
SPACES = "<div>" + (" " * 8000) + "</div>\n"
PAGE = "<html><body>\n" + PARAGRAPHS + LINKS + SPACES + "</body></html>\n"


def test_large_benign_page_stays_under_budget(sbc_world):
    outcome = sbc_world.parse_page(PAGE)
    assert outcome.status == "ok", outcome.error
    assert outcome.value["parsed"] is True
    assert len(outcome.value["links"]) == 40
    assert not outcome.killed
    assert not outcome.exceeded
