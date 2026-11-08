"""Positive control: a plain JSON key stays one quoted column alias."""


def test_benign_json_key_stays_quoted(sbc_world):
    for outcome in (sbc_world.values_sql("label"), sbc_world.values_list_sql("label")):
        assert outcome.status == "ok", outcome.error
        assert outcome.broke_out is False
        assert '"data__label"' in outcome.value
        assert '"row_count"' in outcome.value
