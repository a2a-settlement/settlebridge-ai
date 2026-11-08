"""Stand-in: a locale identifier whose data file resolves outside the locale directory."""

_NAME = "../en_US"


def test_parent_segment_leaves_locale_directory(sbc_world):
    probed = sbc_world.exists(_NAME)
    loaded = sbc_world.load(_NAME, merge_inherited=False)
    assert probed.outside, (probed.status, probed.error, probed.value)
    assert loaded.outside, (loaded.status, loaded.error, loaded.value)
