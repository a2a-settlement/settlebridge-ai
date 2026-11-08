"""Positive control: an ordinary request returns 200 and the expected body."""


def test_direct_request_returns_ok(sbc_world):
    outcome = sbc_world.request("http://origin.invalid/plain")
    assert outcome.status == "ok", outcome.error
    assert outcome.value["status"] == 200
    assert outcome.value["body"] == "ok"
    assert not outcome.leaked
