"""Stand-in: one extra leading slash on the prepared directory leaves the host."""


def test_extra_slash_names_another_host(sbc_world):
    paths = sbc_world.prepare()
    outcome = sbc_world.fetch("/" + paths["directory"])
    assert outcome.status == "ok", outcome.error
    assert outcome.off_host
