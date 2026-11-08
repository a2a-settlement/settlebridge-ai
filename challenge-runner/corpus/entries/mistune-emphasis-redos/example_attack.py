r"""Stand-in attack: CVE-2022-34749 emphasis backtracking.

An opening ``*`` plus a run of backslashes and no closing marker makes the
pre-fix ``ASTERISK_EMPHASIS`` body

    (?:\\[\\*]|[^*])*

backtrack. Thirty-eight backslashes cost several CPU seconds on the base
commit and return immediately on the fix, which matches that body in one pass.
"""

PAYLOAD = "*" + "\\" * 38


def test_unclosed_emphasis_exceeds_cpu_budget(sbc_world):
    outcome = sbc_world.render(PAYLOAD)
    assert outcome.exceeded, (outcome.cpu_seconds, outcome.killed, outcome.error)
