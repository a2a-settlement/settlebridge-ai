"""Negative control: the sibling name is not served when the route stays inside.

The route is created with follow_symlinks off. Both commits refuse the sibling
name in that configuration, so the observer stays silent.
"""


def test_outside_file_is_not_served_without_follow(sbc_world):
    site = sbc_world.prepare(follow_symlinks=False)
    outcome = sbc_world.fetch(site, "../outside.txt")
    assert outcome.status == "ok", outcome.error
    assert outcome.value["status"] == 404
    assert not outcome.leaked
