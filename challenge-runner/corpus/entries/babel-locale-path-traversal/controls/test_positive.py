"""Positive control: an ordinary locale identifier stays inside the locale directory."""


def test_ordinary_locale_stays_inside(sbc_world):
    probed = sbc_world.exists("en_US")
    loaded = sbc_world.load("en_US", merge_inherited=False)
    assert probed.status == "ok", probed.error
    assert loaded.status == "ok", loaded.error
    assert probed.value["paths"]
    assert loaded.value["paths"]
    assert probed.outside is False
    assert loaded.outside is False
