"""Positive control: a reader with repository access can open the private submission."""


def test_reader_reads_private_submission(sbc_world):
    outcome = sbc_world.read_submission("reader", "private")
    assert outcome.status == "ok", outcome.error
    assert outcome.value["status"] == 200
    assert not outcome.leaked
