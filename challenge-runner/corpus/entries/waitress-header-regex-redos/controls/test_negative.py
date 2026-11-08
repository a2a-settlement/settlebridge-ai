"""Negative control: a long valid header is linear work, so the detector stays silent."""

# Every byte of the value is a legal field-vchar. Length alone must not trip
# the observer; only the backtracking input does.
PAYLOAD = b"X-Benign: " + (b"x" * 16000) + b"\r\n"


def test_long_valid_header_stays_under_budget(sbc_world):
    outcome = sbc_world.parse_headers(PAYLOAD)
    assert outcome.status == "ok", outcome.error
    assert not outcome.killed
    assert not outcome.exceeded
