"""Positive control: benign keys render as one quoted attribute list."""


def test_benign_attributes_render(sbc_world):
    outcome = sbc_world.render({"class": "a", "id": "b"})
    assert outcome.status == "ok", outcome.error
    assert outcome.value == '<p class="a" id="b">CANARY</p>'
    assert outcome.broke_out is False
