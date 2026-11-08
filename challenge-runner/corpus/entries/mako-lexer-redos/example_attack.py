r"""Stand-in attack: CVE-2022-40023 lexer backtracking.

``<%x`` plus a run of double quotes and no closing ``>`` makes the pre-1.2.2
``match_tag_start`` attribute pattern

    (?:\s+\w+|\s*=\s*|".*?"|'.*?')*

backtrack. Thirty-eight quotes cost about four CPU seconds on Mako 1.2.1 and
return immediately on the 1.2.2 fix, which uses ``"[^"]*?"`` instead.
"""

PAYLOAD = "<%x" + '"' * 38


def test_unclosed_quoted_tag_exceeds_cpu_budget(sbc_world):
    outcome = sbc_world.parse(PAYLOAD)
    assert outcome.exceeded, (outcome.cpu_seconds, outcome.killed, outcome.error)
