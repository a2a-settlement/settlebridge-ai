"""Stand-in challenger: a static route returns the sibling file."""


def test_static_route_returns_outside_file(sbc_world):
    site = sbc_world.prepare(follow_symlinks=True)
    outcome = sbc_world.fetch(site, "../outside.txt")
    assert outcome.status == "ok", outcome.error
    assert outcome.leaked
