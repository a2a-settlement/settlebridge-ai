"""Positive control: a small field and a small file parse."""


def test_small_form_parses(sbc_world):
    parts = [
        sbc_world.field("name", b"ada"),
        sbc_world.file("doc", "note.txt", b"hello"),
    ]
    outcome = sbc_world.parse(parts)
    assert outcome.status == "ok", outcome.error
    assert outcome.value["accepted"] is True
    assert outcome.value["field_count"] == 1
    assert outcome.value["file_count"] == 1
    assert outcome.value["field_bytes"] == 3
    assert outcome.accepted
    assert not outcome.exceeded
