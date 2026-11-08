"""Stand-in attack: a long label whose contextual check scans every character.

A run of one CONTEXTO codepoint makes each character walk the whole label.
The uncapped library spends seconds of CPU on this input. The length cap
rejects it before that walk, so the same call stays under the threshold.
"""

PAYLOAD = "\u0660" * 14000


def test_long_label_exceeds_cpu_budget(sbc_world):
    outcome = sbc_world.encode(PAYLOAD)
    assert outcome.exceeded, (outcome.cpu_seconds, outcome.killed)
