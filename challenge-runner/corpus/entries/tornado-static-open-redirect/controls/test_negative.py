"""Negative control: the prepared file is served and no redirect is recorded."""


def test_file_is_served(sbc_world):
    paths = sbc_world.prepare()
    outcome = sbc_world.fetch(paths["file"])
    assert outcome.status == "ok", outcome.error
    assert outcome.value["status"] == 200
    assert outcome.value["body"] == "readme"
    assert outcome.value["location"] == ""
    assert not outcome.off_host
