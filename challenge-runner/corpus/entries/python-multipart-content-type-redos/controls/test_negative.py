"""Negative control: a large well-formed Content-Type parses quickly, so the detector stays silent."""

# Closed quoted parameter. Length alone must not trip the observer.
PAYLOAD = 'application/json; param="' + ("\\" * 4000) + '"'


def test_large_benign_content_type_stays_under_budget(sbc_world):
    outcome = sbc_world.parse_content_type(PAYLOAD)
    assert outcome.status == "ok", outcome.error
    assert not outcome.killed
    assert not outcome.exceeded
