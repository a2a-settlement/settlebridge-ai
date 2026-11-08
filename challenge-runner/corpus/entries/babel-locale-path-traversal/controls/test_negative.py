"""Negative control: a dot-dot name is only a filename and stays inside the directory.

The resolved file is ``...dat`` in the locale directory on both revisions, so
the path parse must stay silent.
"""


def test_dotdot_filename_stays_inside(sbc_world):
    probed = sbc_world.exists("..")
    loaded = sbc_world.load("..", merge_inherited=False)
    assert probed.status == "ok", probed.error
    assert loaded.status == "ok", loaded.error
    assert probed.value["paths"]
    assert loaded.value["paths"]
    assert probed.outside is False
    assert loaded.outside is False
