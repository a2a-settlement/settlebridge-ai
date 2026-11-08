"""Positive control: benign extra template data stays a config value."""

_SCHEMA = (
    '{"title": "User", "type": "object", "properties": {"name": {"type": "string"}},'
    ' "required": ["name"]}'
)


def test_benign_template_data_stays_config(sbc_world):
    loaded = sbc_world.load_schema(_SCHEMA)
    assert loaded.status == "ok", loaded.error
    assert loaded.injected is False
    assert "class User" in loaded.value
    outcome = sbc_world.generate({"User": {"allow_extra_fields": True}})
    assert outcome.status == "ok", outcome.error
    assert outcome.injected is False
    assert "class User" in outcome.value
    assert "extra='allow'" in outcome.value
