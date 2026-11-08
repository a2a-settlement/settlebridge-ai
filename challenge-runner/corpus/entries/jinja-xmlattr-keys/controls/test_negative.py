"""Negative control: a malicious value is escaped and stays inside the attribute."""


def test_malicious_value_does_not_break_out(sbc_world):
    outcome = sbc_world.render({"class": '" onerror="x'})
    assert outcome.status == "ok", outcome.error
    assert outcome.value == '<p class="&#34; onerror=&#34;x">CANARY</p>'
    assert outcome.broke_out is False
