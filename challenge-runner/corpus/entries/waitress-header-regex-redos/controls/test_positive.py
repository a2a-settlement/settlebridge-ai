"""Positive control: a normal valid header block parses quickly."""

PAYLOAD = b"Host: localhost\r\n\r\n"


def test_valid_header_parses(sbc_world):
    outcome = sbc_world.parse_headers(PAYLOAD)
    assert outcome.status == "ok", outcome.error
