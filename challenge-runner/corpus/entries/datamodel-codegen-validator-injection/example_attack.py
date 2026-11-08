"""Stand-in: extra template data is emitted as a call in the generated module."""

_SCHEMA = (
    '{"title": "User", "type": "object", "properties": {"name": {"type": "string"}},'
    ' "required": ["name"]}'
)


def test_template_data_reaches_a_call(sbc_world):
    loaded = sbc_world.load_schema(_SCHEMA)
    assert loaded.status == "ok", loaded.error
    outcome = sbc_world.generate(
        {
            "User": {
                "validators": [
                    {"field": "name", "function": "os.system", "mode": "after"},
                ]
            }
        }
    )
    assert outcome.status == "ok", outcome.error
    assert outcome.injected is True
