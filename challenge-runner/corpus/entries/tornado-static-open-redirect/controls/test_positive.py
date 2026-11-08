"""Positive control: a directory redirect stays on this host."""


def test_directory_redirect_stays_on_host(sbc_world):
    paths = sbc_world.prepare()
    outcome = sbc_world.fetch(paths["directory"])
    assert outcome.status == "ok", outcome.error
    assert outcome.value["status"] == 301
    assert outcome.value["location"] == paths["directory"] + "/"
    assert not outcome.off_host
