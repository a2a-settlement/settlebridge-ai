"""Stand-in: a JSON key that closes the column alias before the key ends."""

_KEY = 'injected_name" from "expressions_company"; --'


def test_json_key_alias_is_unquoted(sbc_world):
    for outcome in (sbc_world.values_sql(_KEY), sbc_world.values_list_sql(_KEY)):
        assert outcome.broke_out, (outcome.status, outcome.error, outcome.value)
