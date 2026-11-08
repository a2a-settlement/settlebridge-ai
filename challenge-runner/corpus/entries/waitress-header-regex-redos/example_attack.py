"""Stand-in attack: CVE-2020-5236 header-regex backtracking.

``Bad-header:`` plus legal ``x`` bytes and a trailing invalid control byte
(``\\x10``) makes the 1.4.2 ``HEADER_FIELD`` regex backtrack. Thirty-three
``x`` bytes cost several CPU seconds on that revision and return immediately
on 1.4.1 and on the 1.4.3 fix.
"""

PAYLOAD = b"Bad-header: " + (b"x" * 33) + b"\x10"


def test_invalid_header_exceeds_cpu_budget(sbc_world):
    outcome = sbc_world.parse_headers(PAYLOAD)
    assert outcome.exceeded, (outcome.cpu_seconds, outcome.killed, outcome.error)
