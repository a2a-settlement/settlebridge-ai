"""Positive control: an ordinary request returns 200 and the expected body."""


def test_plain_request_returns_ok(sbc_world):
    outcome = sbc_world.request("plain")
    assert outcome.status == "ok", outcome.error
    assert outcome.value["status"] == 200
    assert outcome.value["body"] == "ok"
