"""Stand-in attack: CVE-2022-40897 package-index backtracking.

An unclosed ``< rel=`` tag followed by a run of spaces makes the pre-fix
``REL`` pattern backtrack. Two thousand spaces cost several CPU seconds on the
base revision and return immediately on the fix, which bounds that whitespace.
"""

PAYLOAD = "< rel=" + (" " * 2000)


def test_unclosed_rel_exceeds_cpu_budget(sbc_world):
    outcome = sbc_world.parse_page(PAYLOAD)
    assert outcome.exceeded, (outcome.cpu_seconds, outcome.killed, outcome.error)
