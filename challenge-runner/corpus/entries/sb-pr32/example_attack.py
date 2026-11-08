"""Stand-in challenger: private submission material is returned to an unentitled user."""


def test_outsider_reads_private_submission(sbc_world):
    outcome = sbc_world.read_submission("outsider", "private")
    assert outcome.status == "ok", outcome.error
    assert outcome.leaked
