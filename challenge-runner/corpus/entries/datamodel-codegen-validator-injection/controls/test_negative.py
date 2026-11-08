"""Negative control: code-like text in a comment stays a comment."""

_SCHEMA = (
    '{"title": "User", "type": "object", "properties": {"name": {"type": "string"}},'
    ' "required": ["name"]}'
)
_TEXT = '__import__("os").system("id")'


def test_comment_text_stays_a_comment(sbc_world):
    loaded = sbc_world.load_schema(_SCHEMA)
    assert loaded.status == "ok", loaded.error
    assert loaded.injected is False
    outcome = sbc_world.generate({"User": {"comment": _TEXT}})
    assert outcome.status == "ok", outcome.error
    assert outcome.injected is False
    in_comment = False
    for line in outcome.value.splitlines():
        _head, mark, tail = line.partition("#")
        if mark and _TEXT in tail:
            in_comment = True
    assert in_comment
