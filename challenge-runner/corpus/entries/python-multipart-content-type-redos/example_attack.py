"""Stand-in attack: CVE-2024-24762 Content-Type backtracking.

An unclosed quoted parameter made of backslashes makes the pre-fix
``parse_options_header`` option pattern backtrack. Thirty-eight backslashes
cost about three CPU seconds on the base revision and return immediately on
the fix, which parses the header with ``email.message.Message``.
"""

PAYLOAD = 'application/x-www-form-urlencoded; param="' + ("\\" * 38)


def test_unclosed_quoted_parameter_exceeds_cpu_budget(sbc_world):
    outcome = sbc_world.parse_content_type(PAYLOAD)
    assert outcome.exceeded, (outcome.cpu_seconds, outcome.killed, outcome.error)
