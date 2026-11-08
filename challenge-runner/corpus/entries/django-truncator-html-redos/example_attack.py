"""Stand-in attack: incomplete fix for the truncator HTML backtracking bug.

``Truncator.words(..., html=True)`` still uses a regex that retries a run of
unclosed ``<`` characters. Forty-eight thousand of them cost several CPU
seconds on the length-cap revision and return immediately once words are
scanned without that regex.
"""

PAYLOAD = "<" * 48000


def test_unclosed_brackets_exceed_cpu_budget(sbc_world):
    outcome = sbc_world.words(PAYLOAD, 1)
    assert outcome.exceeded, (outcome.cpu_seconds, outcome.killed, outcome.error)
