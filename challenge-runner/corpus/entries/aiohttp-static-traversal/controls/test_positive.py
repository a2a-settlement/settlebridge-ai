"""Positive control: a file inside the static directory is served."""


def test_public_file_is_served(sbc_world):
    site = sbc_world.prepare(follow_symlinks=True)
    outcome = sbc_world.fetch(site, "public.txt")
    assert outcome.status == "ok", outcome.error
    assert outcome.value["status"] == 200
    assert outcome.value["body"] == "ok"
    assert not outcome.leaked
