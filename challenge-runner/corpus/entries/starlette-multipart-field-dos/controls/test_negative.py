"""Negative control: a large file part stays under the field budget.

Same number of bytes as the stand-in, but the part has a filename, so the
library does not retain it as a form field. Length alone must not trip the
observer.
"""

BLOB = b"x" * (2 * 1024 * 1024)


def test_large_file_stays_under_budget(sbc_world):
    outcome = sbc_world.parse([sbc_world.file("upload", "blob.bin", BLOB)])
    assert outcome.status == "ok", outcome.error
    assert outcome.value["accepted"] is True
    assert outcome.value["file_count"] == 1
    assert outcome.value["field_bytes"] == 0
    assert not outcome.killed
    assert not outcome.exceeded
