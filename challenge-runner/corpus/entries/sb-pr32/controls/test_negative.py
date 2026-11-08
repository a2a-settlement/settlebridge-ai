"""Negative control: a signed-in user without access gets no private bounty body."""


def test_outsider_private_bounty_stays_hidden(sbc_world):
    outcome = sbc_world.read_bounty("outsider", "private")
    assert outcome.status == "ok", outcome.error
    assert outcome.value["status"] == 404
    assert not outcome.leaked
