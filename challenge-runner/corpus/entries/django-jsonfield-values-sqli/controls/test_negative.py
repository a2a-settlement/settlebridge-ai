"""Negative control: a keyword alias stays quoted, and a bound key stays out of SQL."""

_KEY = 'injected_name" from "expressions_company"; --'


def test_bound_key_and_keyword_alias_stay_silent(sbc_world):
    bound = sbc_world.filter_sql(_KEY)
    assert bound.status == "ok", bound.error
    assert bound.broke_out is False
    assert _KEY not in bound.value
    assert "%s" in bound.value

    for outcome in (sbc_world.values_sql("select"), sbc_world.values_list_sql("select")):
        assert outcome.status == "ok", outcome.error
        assert outcome.broke_out is False
        assert '"data__select"' in outcome.value
