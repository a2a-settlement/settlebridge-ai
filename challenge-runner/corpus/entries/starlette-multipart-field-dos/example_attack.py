"""Stand-in: a non-file field larger than the default one-mebibyte threshold.

The part has no filename. A parser that retains the whole field records
``memory_mb``. A parser that rejects the part retains nothing, so the observer
stays silent.
"""

FIELD = b"x" * (2 * 1024 * 1024)


def test_unbounded_field_exceeds_memory_budget(sbc_world):
    outcome = sbc_world.parse([sbc_world.field("note", FIELD)])
    assert outcome.exceeded, (outcome.value, outcome.error)
